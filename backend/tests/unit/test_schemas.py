"""Unit tests for Stage 2: Intermediate schemas, enums, and immutability."""
import pytest
from datetime import date, datetime, timedelta, timezone
from pydantic import ValidationError

from app.schemas.enums import Gender, DocumentType, ProcessingStatus
from app.schemas.intermediate import (
    PatientDemographics,
    ClinicalDocumentRecord,
    SourceMetadata,
    IntermediateRecord,
)
from app.schemas.audit import TransformationRecord, AuditLogEntry, QuarantinedRecord


def test_patient_demographics_valid():
    """Verify that valid patient demographics instantiate properly with composite key."""
    patient = PatientDemographics(
        mrn="MRN-1001",
        original_mrn="  MRN-1001  ",
        facility_id="FAC-NORTH",
        first_name="Jane",
        last_name="Doe",
        date_of_birth=date(1985, 4, 12),
        gender=Gender.FEMALE,
        original_gender="F",
    )
    assert patient.mrn == "MRN-1001"
    assert patient.original_mrn == "  MRN-1001  "
    assert patient.facility_id == "FAC-NORTH"
    assert patient.identity_key == ("FAC-NORTH", "MRN-1001")
    assert patient.gender == Gender.FEMALE


def test_patient_demographics_immutability():
    """Verify that frozen=True prevents direct field mutation."""
    patient = PatientDemographics(
        mrn="MRN-1001",
        first_name="Jane",
        last_name="Doe",
        date_of_birth=date(1985, 4, 12),
    )
    with pytest.raises(ValidationError):
        patient.first_name = "Janet"  # type: ignore


def test_patient_demographics_rejects_future_dob():
    """Verify that a future birth date is rejected."""
    future_date = date.today() + timedelta(days=1)
    with pytest.raises(ValidationError, match="cannot be in the future"):
        PatientDemographics(
            mrn="MRN-1001",
            first_name="Baby",
            last_name="Future",
            date_of_birth=future_date,
        )


def test_patient_demographics_rejects_impossible_lifespan():
    """Verify that a birth date >130 years ago is rejected."""
    ancient_date = date(1880, 1, 1)
    with pytest.raises(ValidationError, match="exceeds reasonable human lifespan"):
        PatientDemographics(
            mrn="MRN-1001",
            first_name="Ancient",
            last_name="Person",
            date_of_birth=ancient_date,
        )


def test_clinical_document_timezone_enforcement():
    """Verify that naive datetimes are converted to timezone-aware UTC."""
    naive_dt = datetime(2024, 1, 15, 10, 30, 0)
    doc = ClinicalDocumentRecord(
        document_id="DOC-1",
        encounter_id="ENC-1",
        document_type=DocumentType.CLINICAL_NOTE,
        effective_date=naive_dt,
        title="General Checkup",
        text_content="Patient reports no acute symptoms.",
        content_hash="abc123hash",
    )
    assert doc.effective_date.tzinfo is not None
    assert doc.effective_date.tzinfo == timezone.utc


def test_intermediate_record_validation():
    """Verify full IntermediateRecord construction and document date validation."""
    patient = PatientDemographics(
        mrn="MRN-555",
        first_name="Alice",
        last_name="Smith",
        date_of_birth=date(1990, 6, 1),
        gender=Gender.FEMALE,
    )
    doc_valid = ClinicalDocumentRecord(
        document_id="DOC-A",
        document_type=DocumentType.LAB_REPORT,
        effective_date=datetime(2023, 5, 1, 12, 0, tzinfo=timezone.utc),
        title="Lipid Panel",
        text_content="Cholesterol: 180 mg/dL",
        content_hash="hash_valid",
    )
    metadata = SourceMetadata(source_file="sample.json", source_format="JSON")

    record = IntermediateRecord(
        record_id="REC-001",
        patient=patient,
        documents=[doc_valid],
        metadata=metadata,
    )
    assert record.record_id == "REC-001"
    assert len(record.documents) == 1

    # Now verify that a document date preceding DOB fails validation
    doc_before_dob = ClinicalDocumentRecord(
        document_id="DOC-B",
        document_type=DocumentType.CLINICAL_NOTE,
        effective_date=datetime(1980, 1, 1, 12, 0, tzinfo=timezone.utc),
        title="Pre-birth Note",
        text_content="Impossible encounter before birth.",
        content_hash="hash_invalid",
    )
    with pytest.raises(ValidationError, match="cannot precede patient birth date"):
        IntermediateRecord(
            record_id="REC-002",
            patient=patient,
            documents=[doc_before_dob],
            metadata=metadata,
        )


def test_clinical_document_optional_effective_date():
    """Verify that ClinicalDocumentRecord allows effective_date to be None."""
    doc = ClinicalDocumentRecord(
        document_id="DOC-NODATE",
        document_type=DocumentType.CLINICAL_NOTE,
        effective_date=None,
        title="Note without date",
        text_content="Undated clinical note.",
        content_hash="hash_nodate",
    )
    assert doc.effective_date is None


def test_audit_log_entry_construction():
    """Verify audit log entry and transformation record structure."""
    trans = TransformationRecord(
        field_name="gender",
        original_value="F",
        normalized_value="FEMALE",
        reason="Mapped from unambiguous abbreviation",
    )
    entry = AuditLogEntry(
        audit_id="AUD-001",
        record_id="REC-001",
        source_file="test.json",
        sanitized_mrn="MRN-555",
        status=ProcessingStatus.NORMALIZED_WITH_WARNINGS,
        transformations=[trans],
        warnings=["Non-standard facility code"],
    )
    assert entry.status == ProcessingStatus.NORMALIZED_WITH_WARNINGS
    assert len(entry.transformations) == 1
    assert entry.processed_at.tzinfo == timezone.utc
