"""Patient identity resolution, registry, and deduplication logic.

Enforces:
- Identity matching using composite (facility_id, MRN).
- Same MRN with different name or DOB is CONFLICT_DETECTED even when facility_id is missing.
- Preservation of encounter_id and document_type in clinical document deduplication.
- Safe accumulation of documents per patient, building immutable IntermediateRecords at the end.
- Design supporting persistent storage backends in future tasks.
"""
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Dict, List, Optional, Set, Tuple
import uuid

from app.schemas.enums import DocumentType
from app.schemas.intermediate import (
    ClinicalDocumentRecord,
    IntermediateRecord,
    PatientDemographics,
    SourceMetadata,
)


def compute_document_hash(
    encounter_id: Optional[str],
    document_type: DocumentType,
    effective_date: Optional[datetime],
    text_content: str,
) -> str:
    """Computes a deterministic SHA-256 hash preserving encounter_id and document_type."""
    clean_encounter = (encounter_id or "").strip()
    clean_type = document_type.value
    clean_date = effective_date.isoformat() if effective_date is not None else ""
    clean_text = text_content.strip()
    raw = f"{clean_encounter}|{clean_type}|{clean_date}|{clean_text}"
    return sha256(raw.encode("utf-8")).hexdigest()


def compute_record_id(facility_id: Optional[str], mrn: str) -> str:
    """Computes a deterministic record_id based on composite identity (facility_id, MRN)."""
    facility_part = (facility_id or "NO_FAC").upper()
    record_uuid = sha256(f"{facility_part}:{mrn}".encode("utf-8")).hexdigest()[:12].upper()
    return f"REC-{record_uuid}"


@dataclass
class RegisteredPatient:
    """Internal registry entry for accumulating documents for a patient."""
    demographics: PatientDemographics
    record_id: str
    documents: List[ClinicalDocumentRecord] = field(default_factory=list)
    document_hashes: Set[str] = field(default_factory=set)
    source_metadata: Optional[SourceMetadata] = None


@dataclass(frozen=True)
class DedupResult:
    """Result of registering/deduplicating an incoming record."""
    status: str  # "NEW_PATIENT", "DOCUMENT_APPENDED", "DUPLICATE_IGNORED", "CONFLICT_DETECTED"
    message: str
    record_id: Optional[str] = None
    existing_patient: Optional[PatientDemographics] = None
    conflict_reason: Optional[str] = None
    document: Optional[ClinicalDocumentRecord] = None


class PatientRegistry:
    """In-memory registry managing patient identities and document accumulation.
    
    Can be replaced or backed with an SQLite/database repository in future tasks.
    """

    def __init__(self) -> None:
        # Maps (facility_id, mrn) -> RegisteredPatient
        self._patients_by_facility_mrn: Dict[Tuple[Optional[str], str], RegisteredPatient] = {}
        # Maps mrn -> list of RegisteredPatient (for cross-facility conflict checking)
        self._patients_by_mrn: Dict[str, List[RegisteredPatient]] = {}

    def check_or_register(
        self,
        patient: PatientDemographics,
        document: ClinicalDocumentRecord,
        metadata: SourceMetadata,
    ) -> DedupResult:
        """Evaluates incoming patient and document for deduplication and identity conflicts.
        
        Rules:
        1. Same MRN with different name or DOB is CONFLICT_DETECTED (even if facility_id differs or is missing).
        2. Exact document content hash match for existing patient is DUPLICATE_IGNORED.
        3. New document for matching patient is DOCUMENT_APPENDED.
        4. Unseen patient is NEW_PATIENT.
        """
        mrn = patient.mrn
        target_record_id = compute_record_id(patient.facility_id, mrn)

        # 1. Global MRN collision check across all registered patients
        if mrn in self._patients_by_mrn:
            for registered in self._patients_by_mrn[mrn]:
                reg_demo = registered.demographics
                # Compare names and date of birth (case-insensitive)
                names_match = (
                    reg_demo.first_name.strip().lower() == patient.first_name.strip().lower()
                    and reg_demo.last_name.strip().lower() == patient.last_name.strip().lower()
                )
                dob_matches = reg_demo.date_of_birth == patient.date_of_birth

                if not (names_match and dob_matches):
                    # CONFLICT DETECTED! Same MRN with different demographic identity
                    diff_parts = []
                    if not names_match:
                        diff_parts.append(
                            f"Name mismatch (existing: '{reg_demo.first_name} {reg_demo.last_name}', "
                            f"incoming: '{patient.first_name} {patient.last_name}')"
                        )
                    if not dob_matches:
                        diff_parts.append(
                            f"DOB mismatch (existing: '{reg_demo.date_of_birth}', "
                            f"incoming: '{patient.date_of_birth}')"
                        )
                    conflict_reason = (
                        f"Identity conflict detected for MRN '{mrn}': " + "; ".join(diff_parts) +
                        ". Patients must not be merged."
                    )
                    return DedupResult(
                        status="CONFLICT_DETECTED",
                        message=conflict_reason,
                        record_id=None,
                        existing_patient=reg_demo,
                        conflict_reason=conflict_reason,
                    )

        # 2. Check composite identity key (facility_id, mrn)
        key = patient.identity_key
        if key in self._patients_by_facility_mrn:
            reg_patient = self._patients_by_facility_mrn[key]
            # Check document deduplication
            if document.content_hash in reg_patient.document_hashes:
                return DedupResult(
                    status="DUPLICATE_IGNORED",
                    message=(
                        f"Document '{document.document_id}' (hash: {document.content_hash[:8]}) "
                        f"is an exact duplicate for patient MRN '{mrn}'; skipped."
                    ),
                    record_id=reg_patient.record_id,
                    existing_patient=reg_patient.demographics,
                    document=document,
                )
            else:
                # Append new clinical document to this patient
                reg_patient.documents.append(document)
                reg_patient.document_hashes.add(document.content_hash)
                return DedupResult(
                    status="DOCUMENT_APPENDED",
                    message=(
                        f"Document '{document.document_id}' appended to existing patient MRN '{mrn}'."
                    ),
                    record_id=reg_patient.record_id,
                    existing_patient=reg_patient.demographics,
                    document=document,
                )

        # 3. New patient registration
        new_entry = RegisteredPatient(
            demographics=patient,
            record_id=target_record_id,
            documents=[document],
            document_hashes={document.content_hash},
            source_metadata=metadata,
        )
        self._patients_by_facility_mrn[key] = new_entry
        self._patients_by_mrn.setdefault(mrn, []).append(new_entry)

        return DedupResult(
            status="NEW_PATIENT",
            message=f"New patient registered with MRN '{mrn}' and document '{document.document_id}'.",
            record_id=target_record_id,
            existing_patient=patient,
            document=document,
        )

    def build_intermediate_records(self) -> List[IntermediateRecord]:
        """Constructs final immutable IntermediateRecord instances for all registered patients."""
        records: List[IntermediateRecord] = []
        for (facility_id, mrn), reg in self._patients_by_facility_mrn.items():
            metadata = reg.source_metadata or SourceMetadata(
                source_file="batch_registry",
                source_format="HYBRID",
            )

            record = IntermediateRecord(
                record_id=reg.record_id,
                patient=reg.demographics,
                documents=list(reg.documents),
                metadata=metadata,
            )
            records.append(record)

        return records

    def clear(self) -> None:
        """Clear all in-memory entries."""
        self._patients_by_facility_mrn.clear()
        self._patients_by_mrn.clear()
