"""Unit tests for Stage 5: Deduplication, identity conflicts, and patient registry."""
from datetime import date, datetime, timezone
import pytest
from app.schemas.enums import DocumentType, Gender
from app.schemas.intermediate import (
    ClinicalDocumentRecord,
    PatientDemographics,
    SourceMetadata,
)
from app.ingestion.dedup import PatientRegistry, compute_document_hash


def _create_sample_patient(mrn: str, first_name: str, last_name: str, dob: date, facility_id: str = "HOSP_A"):
    return PatientDemographics(
        mrn=mrn,
        first_name=first_name,
        last_name=last_name,
        date_of_birth=dob,
        facility_id=facility_id,
        gender=Gender.MALE,
    )


def _create_sample_doc(doc_id: str, encounter_id: str, doc_type: DocumentType, text: str):
    effective_dt = datetime(2024, 1, 10, 10, 0, tzinfo=timezone.utc)
    chash = compute_document_hash(encounter_id, doc_type, effective_dt, text)
    return ClinicalDocumentRecord(
        document_id=doc_id,
        encounter_id=encounter_id,
        document_type=doc_type,
        effective_date=effective_dt,
        title="Test Document",
        text_content=text,
        content_hash=chash,
    )


def test_new_patient_registration():
    """Verify first encounter with a patient returns NEW_PATIENT."""
    registry = PatientRegistry()
    patient = _create_sample_patient("MRN-101", "John", "Doe", date(1980, 1, 1))
    doc = _create_sample_doc("D1", "ENC-1", DocumentType.CLINICAL_NOTE, "Initial note")
    meta = SourceMetadata(source_file="file1.json", source_format="JSON")

    res = registry.check_or_register(patient, doc, meta)
    assert res.status == "NEW_PATIENT"

    records = registry.build_intermediate_records()
    assert len(records) == 1
    assert records[0].patient.mrn == "MRN-101"
    assert len(records[0].documents) == 1


def test_document_appended_for_same_patient():
    """Verify subsequent different document for existing patient appends to record."""
    registry = PatientRegistry()
    patient = _create_sample_patient("MRN-101", "John", "Doe", date(1980, 1, 1))
    doc1 = _create_sample_doc("D1", "ENC-1", DocumentType.CLINICAL_NOTE, "Initial note")
    doc2 = _create_sample_doc("D2", "ENC-2", DocumentType.LAB_REPORT, "Lab findings: normal")
    meta = SourceMetadata(source_file="file1.json", source_format="JSON")

    res1 = registry.check_or_register(patient, doc1, meta)
    assert res1.status == "NEW_PATIENT"

    res2 = registry.check_or_register(patient, doc2, meta)
    assert res2.status == "DOCUMENT_APPENDED"

    records = registry.build_intermediate_records()
    assert len(records) == 1
    assert len(records[0].documents) == 2
    assert records[0].documents[0].document_type == DocumentType.CLINICAL_NOTE
    assert records[0].documents[1].document_type == DocumentType.LAB_REPORT


def test_exact_duplicate_ignored():
    """Verify identical document for same patient returns DUPLICATE_IGNORED."""
    registry = PatientRegistry()
    patient = _create_sample_patient("MRN-101", "John", "Doe", date(1980, 1, 1))
    doc = _create_sample_doc("D1", "ENC-1", DocumentType.CLINICAL_NOTE, "Initial note")
    meta = SourceMetadata(source_file="file1.json", source_format="JSON")

    registry.check_or_register(patient, doc, meta)
    # Register again with identical payload
    dup_res = registry.check_or_register(patient, doc, meta)
    assert dup_res.status == "DUPLICATE_IGNORED"

    records = registry.build_intermediate_records()
    assert len(records) == 1
    assert len(records[0].documents) == 1  # Not duplicated


def test_conflict_detected_different_name_same_mrn():
    """Verify same MRN with different patient name triggers CONFLICT_DETECTED and is not merged."""
    registry = PatientRegistry()
    patient1 = _create_sample_patient("MRN-500", "Alice", "Smith", date(1990, 5, 10))
    patient2 = _create_sample_patient("MRN-500", "Bob", "Williams", date(1990, 5, 10))

    doc1 = _create_sample_doc("D1", "ENC-1", DocumentType.CLINICAL_NOTE, "Note Alice")
    doc2 = _create_sample_doc("D2", "ENC-2", DocumentType.CLINICAL_NOTE, "Note Bob")
    meta = SourceMetadata(source_file="file1.json", source_format="JSON")

    res1 = registry.check_or_register(patient1, doc1, meta)
    assert res1.status == "NEW_PATIENT"

    res2 = registry.check_or_register(patient2, doc2, meta)
    assert res2.status == "CONFLICT_DETECTED"
    assert "Identity conflict detected" in res2.conflict_reason
    assert "Name mismatch" in res2.conflict_reason

    # Ensure patient2 was not merged
    records = registry.build_intermediate_records()
    assert len(records) == 1
    assert records[0].patient.first_name == "Alice"


def test_conflict_detected_different_dob_same_mrn_missing_facility():
    """Verify same MRN with different DOB is CONFLICT_DETECTED even without facility_id."""
    registry = PatientRegistry()
    patient1 = PatientDemographics(
        mrn="MRN-777",
        first_name="Jane",
        last_name="Doe",
        date_of_birth=date(1985, 3, 1),
        facility_id=None,
    )
    patient2 = PatientDemographics(
        mrn="MRN-777",
        first_name="Jane",
        last_name="Doe",
        date_of_birth=date(1992, 8, 15),
        facility_id=None,
    )
    doc1 = _create_sample_doc("D1", "ENC-1", DocumentType.CLINICAL_NOTE, "Note 1")
    doc2 = _create_sample_doc("D2", "ENC-2", DocumentType.CLINICAL_NOTE, "Note 2")
    meta = SourceMetadata(source_file="file1.json", source_format="JSON")

    registry.check_or_register(patient1, doc1, meta)
    res = registry.check_or_register(patient2, doc2, meta)

    assert res.status == "CONFLICT_DETECTED"
    assert "DOB mismatch" in res.conflict_reason


def test_dedup_preserves_encounter_id_and_document_type():
    """Verify encounter_id and document_type affect content_hash so they are never lost."""
    dt = datetime(2024, 1, 10, 10, 0, tzinfo=timezone.utc)
    hash1 = compute_document_hash("ENC-1", DocumentType.CLINICAL_NOTE, dt, "Clinical text")
    hash2 = compute_document_hash("ENC-2", DocumentType.CLINICAL_NOTE, dt, "Clinical text")
    hash3 = compute_document_hash("ENC-1", DocumentType.DISCHARGE_SUMMARY, dt, "Clinical text")

    assert hash1 != hash2
    assert hash1 != hash3

    # Effective date None produces valid deterministic hash
    hash_none = compute_document_hash("ENC-1", DocumentType.CLINICAL_NOTE, None, "Clinical text")
    assert isinstance(hash_none, str) and len(hash_none) == 64
    assert hash_none != hash1
