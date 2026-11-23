"""Orchestration pipeline for clinical data ingestion and cleaning.

Coordinates:
- Format sniffing and parsing (JSON, CSV, Plain-text)
- Data cleaning and normalization (dates, gender, MRN)
- Schema validation via Pydantic v2
- Deduplication and identity conflict detection via PatientRegistry
- Structured audit trail logging with exact record_id correlation
- Batch result generation
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import uuid

from app.core.exceptions import DateParsingError, IngestionError, SchemaValidationError
from app.schemas.enums import DocumentType, ProcessingStatus
from app.schemas.intermediate import (
    ClinicalDocumentRecord,
    PatientDemographics,
    SourceMetadata,
)
from app.schemas.audit import IngestionBatchResult, QuarantinedRecord, TransformationRecord
from app.ingestion.audit import AuditLogger
from app.ingestion.dedup import PatientRegistry, compute_document_hash
from app.ingestion.normalizers import (
    normalize_date,
    normalize_datetime,
    normalize_gender,
    normalize_mrn,
)
from app.ingestion.parsers import BaseParser, RawClinicalRecord, get_parser_for_file


DOC_TYPE_MAP = {
    "clinical_note": DocumentType.CLINICAL_NOTE,
    "clinical note": DocumentType.CLINICAL_NOTE,
    "note": DocumentType.CLINICAL_NOTE,
    "progress_note": DocumentType.CLINICAL_NOTE,
    "lab_report": DocumentType.LAB_REPORT,
    "lab report": DocumentType.LAB_REPORT,
    "lab": DocumentType.LAB_REPORT,
    "imaging": DocumentType.IMAGING,
    "radiology": DocumentType.IMAGING,
    "x-ray": DocumentType.IMAGING,
    "mri": DocumentType.IMAGING,
    "ct": DocumentType.IMAGING,
    "discharge_summary": DocumentType.DISCHARGE_SUMMARY,
    "discharge summary": DocumentType.DISCHARGE_SUMMARY,
    "discharge": DocumentType.DISCHARGE_SUMMARY,
    "other": DocumentType.OTHER,
}

DOCUMENT_DEFAULT_TITLES = {
    DocumentType.LAB_REPORT: "Laboratory Diagnostic Report",
    DocumentType.IMAGING: "Diagnostic Medical Imaging Report",
    DocumentType.DISCHARGE_SUMMARY: "Hospital Inpatient Discharge Summary",
    DocumentType.CLINICAL_NOTE: "Clinical Encounter Progress Note",
    DocumentType.OTHER: "Clinical Document",
}


def _map_document_type(raw_type: Optional[str]) -> DocumentType:
    """Maps arbitrary document type string to canonical DocumentType enum."""
    if not raw_type:
        return DocumentType.CLINICAL_NOTE
    cleaned = str(raw_type).strip().lower()
    return DOC_TYPE_MAP.get(cleaned, DocumentType.OTHER)


def _determine_document_title(raw_title: Optional[str], doc_type: DocumentType) -> str:
    """Generates document title reflecting clinical document type when not explicitly supplied."""
    if raw_title and str(raw_title).strip():
        return str(raw_title).strip()
    return DOCUMENT_DEFAULT_TITLES.get(doc_type, "Clinical Document")


class IngestionPipeline:
    """Main ingestion pipeline orchestrator."""

    def __init__(self, registry: Optional[PatientRegistry] = None) -> None:
        self.registry = registry if registry is not None else PatientRegistry()
        self.logger = AuditLogger()

    def process_raw_records(
        self,
        raw_records: List[RawClinicalRecord],
        source_format: str,
    ) -> IngestionBatchResult:
        """Processes a list of raw extracted records through the complete pipeline."""
        start_time = datetime.now(timezone.utc)
        quarantined_records: List[QuarantinedRecord] = []
        duplicate_count = 0

        for rec in raw_records:
            source_file = rec.source_file
            source_rec_id = rec.source_record_id

            # 1. Handle unassigned documents (missing patient identity)
            if rec.is_unassigned:
                reason = rec.unassigned_reason or "Unassigned clinical document: Missing patient identity"
                self.logger.log(
                    source_file=source_file,
                    source_record_id=source_rec_id,
                    status=ProcessingStatus.QUARANTINED,
                    errors=[reason],
                )
                quarantined_records.append(
                    QuarantinedRecord(
                        quarantine_id=f"QRN-{uuid.uuid4().hex[:10].upper()}",
                        source_file=source_file,
                        source_record_id=source_rec_id,
                        raw_data=rec.raw_payload,
                        reason=reason,
                    )
                )
                continue

            transformations: List[TransformationRecord] = []
            warnings: List[str] = []

            # 2. Normalize patient demographics
            try:
                raw_mrn = rec.patient_data.get("mrn")
                if not raw_mrn:
                    raise SchemaValidationError("Missing required patient identifier: MRN")

                mrn_res = normalize_mrn(raw_mrn)
                transformations.extend(mrn_res.transformations)
                warnings.extend(mrn_res.warnings)

                raw_dob = rec.patient_data.get("date_of_birth")
                if not raw_dob:
                    raise SchemaValidationError("Missing required patient birth date (DOB)")

                dob_res = normalize_date(raw_dob, is_dob=True)
                transformations.extend(dob_res.transformations)
                warnings.extend(dob_res.warnings)

                raw_gender = rec.patient_data.get("gender")
                gender_res = normalize_gender(raw_gender)
                transformations.extend(gender_res.transformations)
                warnings.extend(gender_res.warnings)

                first_name = str(rec.patient_data.get("first_name", "")).strip()
                last_name = str(rec.patient_data.get("last_name", "")).strip()
                if not first_name or not last_name:
                    raise SchemaValidationError("Patient first name and last name are required")

                facility_id = rec.patient_data.get("facility_id")
                if facility_id:
                    facility_id = str(facility_id).strip()

                patient = PatientDemographics(
                    mrn=mrn_res.value,
                    original_mrn=mrn_res.original,
                    facility_id=facility_id,
                    first_name=first_name,
                    last_name=last_name,
                    date_of_birth=dob_res.value,
                    gender=gender_res.value,
                    original_gender=gender_res.original,
                    national_id=rec.patient_data.get("national_id"),
                    phone=rec.patient_data.get("phone"),
                    email=rec.patient_data.get("email"),
                    address=rec.patient_data.get("address"),
                )

                # 3. Normalize clinical document fields
                raw_text = rec.document_data.get("text_content")
                if not raw_text or not str(raw_text).strip():
                    raise SchemaValidationError("Clinical document has empty text content")

                raw_eff_date = rec.document_data.get("effective_date")
                effective_dt = None
                if raw_eff_date:
                    try:
                        dt_res = normalize_datetime(raw_eff_date)
                        effective_dt = dt_res.value
                        transformations.extend(dt_res.transformations)
                        warnings.extend(dt_res.warnings)
                    except Exception as date_err:
                        effective_dt = None
                        warnings.append(
                            f"Invalid document date '{raw_eff_date}' ({date_err}); kept effective_date as None."
                        )
                else:
                    # Missing date: keep effective_date as None; never substitute ingestion timestamp
                    effective_dt = None
                    warnings.append(
                        "Document effective date was not provided in source; kept effective_date as None."
                    )

                # Ensure encounter date does not precede DOB (when effective_date is present)
                if effective_dt is not None and effective_dt.date() < patient.date_of_birth:
                    raise SchemaValidationError(
                        f"Document date {effective_dt.date()} cannot precede patient DOB {patient.date_of_birth}"
                    )

                doc_type = _map_document_type(rec.document_data.get("document_type"))
                title = _determine_document_title(rec.document_data.get("title"), doc_type)

                encounter_id = rec.document_data.get("encounter_id")
                if encounter_id:
                    encounter_id = str(encounter_id).strip()

                content_hash = compute_document_hash(
                    encounter_id=encounter_id,
                    document_type=doc_type,
                    effective_date=effective_dt,
                    text_content=raw_text,
                )

                doc_id = rec.document_data.get("document_id") or f"DOC-{uuid.uuid4().hex[:10].upper()}"

                clinical_doc = ClinicalDocumentRecord(
                    document_id=doc_id,
                    encounter_id=encounter_id,
                    document_type=doc_type,
                    effective_date=effective_dt,
                    title=title,
                    text_content=str(raw_text).strip(),
                    author=rec.document_data.get("author"),
                    facility=rec.document_data.get("facility") or facility_id,
                    status=rec.document_data.get("status", "final"),
                    structured_data=rec.document_data.get("structured_data", {}),
                    diagnoses=rec.document_data.get("diagnoses", []),
                    content_hash=content_hash,
                )

                # 4. Ingestion provenance metadata
                metadata = SourceMetadata(
                    source_file=source_file,
                    source_format=source_format,
                    source_record_id=source_rec_id,
                )

                # 5. Registry deduplication & conflict evaluation
                dedup_res = self.registry.check_or_register(patient, clinical_doc, metadata)

                if dedup_res.status == "CONFLICT_DETECTED":
                    self.logger.log(
                        source_file=source_file,
                        source_record_id=source_rec_id,
                        status=ProcessingStatus.CONFLICT_DETECTED,
                        sanitized_mrn=patient.mrn,
                        facility_id=patient.facility_id,
                        errors=[dedup_res.conflict_reason],
                        warnings=warnings,
                        transformations=transformations,
                    )
                    quarantined_records.append(
                        QuarantinedRecord(
                            quarantine_id=f"QRN-{uuid.uuid4().hex[:10].upper()}",
                            source_file=source_file,
                            source_record_id=source_rec_id,
                            raw_data=rec.raw_payload,
                            reason=dedup_res.conflict_reason,
                        )
                    )
                elif dedup_res.status == "DUPLICATE_IGNORED":
                    duplicate_count += 1
                    self.logger.log(
                        source_file=source_file,
                        source_record_id=source_rec_id,
                        record_id=dedup_res.record_id,
                        status=ProcessingStatus.DUPLICATE_IGNORED,
                        sanitized_mrn=patient.mrn,
                        facility_id=patient.facility_id,
                        warnings=warnings + [dedup_res.message],
                        transformations=transformations,
                    )
                else:
                    # NEW_PATIENT or DOCUMENT_APPENDED
                    final_status = (
                        ProcessingStatus.NORMALIZED_WITH_WARNINGS
                        if warnings
                        else ProcessingStatus.SUCCESS
                    )
                    self.logger.log(
                        source_file=source_file,
                        source_record_id=source_rec_id,
                        record_id=dedup_res.record_id,
                        status=final_status,
                        sanitized_mrn=patient.mrn,
                        facility_id=patient.facility_id,
                        transformations=transformations,
                        warnings=warnings,
                    )

            except Exception as e:
                err_msg = f"Validation or processing error: {e}"
                self.logger.log(
                    source_file=source_file,
                    source_record_id=source_rec_id,
                    status=ProcessingStatus.VALIDATION_FAILED,
                    sanitized_mrn=rec.patient_data.get("mrn"),
                    facility_id=rec.patient_data.get("facility_id"),
                    errors=[err_msg],
                    warnings=warnings,
                    transformations=transformations,
                )
                quarantined_records.append(
                    QuarantinedRecord(
                        quarantine_id=f"QRN-{uuid.uuid4().hex[:10].upper()}",
                        source_file=source_file,
                        source_record_id=source_rec_id,
                        raw_data=rec.raw_payload,
                        reason=err_msg,
                    )
                )

        successful_records = self.registry.build_intermediate_records()
        end_time = datetime.now(timezone.utc)

        return IngestionBatchResult(
            total_processed=len(raw_records),
            successful_records=successful_records,
            quarantined_records=quarantined_records,
            duplicate_count=duplicate_count,
            audit_logs=self.logger.get_entries(),
            started_at=start_time,
            completed_at=end_time,
        )

    def ingest_content(
        self,
        content: Union[str, bytes],
        file_name: str,
        parser: Optional[BaseParser] = None,
    ) -> IngestionBatchResult:
        """Parses and ingests content from a raw string or byte stream."""
        chosen_parser = parser or get_parser_for_file(file_name)
        fmt = file_name.split(".")[-1].upper() if "." in file_name else "TXT"
        raw_records = chosen_parser.parse(content, file_name)
        return self.process_raw_records(raw_records, source_format=fmt)

    def ingest_file(self, file_path: Union[str, Path]) -> IngestionBatchResult:
        """Reads and ingests a single file from disk."""
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        return self.ingest_content(content, path.name)

    def ingest_files(self, file_paths: List[Union[str, Path]]) -> IngestionBatchResult:
        """Ingests multiple files in batch, sharing the single patient registry and audit trail."""
        all_raw_records: List[RawClinicalRecord] = []
        fmt_summary = []

        for p in file_paths:
            path = Path(p)
            fmt = path.suffix.lstrip(".").upper() or "TXT"
            fmt_summary.append(fmt)
            parser = get_parser_for_file(path.name)
            content = path.read_text(encoding="utf-8")
            all_raw_records.extend(parser.parse(content, path.name))

        combined_format = "/".join(sorted(set(fmt_summary)))
        return self.process_raw_records(all_raw_records, source_format=combined_format)
