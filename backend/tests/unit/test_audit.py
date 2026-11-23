"""Unit tests for Stage 6: Audit trail and pipeline orchestration."""
import json
import pytest
from app.schemas.enums import ProcessingStatus, DocumentType
from app.schemas.audit import TransformationRecord
from app.ingestion.audit import AuditLogger
from app.ingestion.pipeline import IngestionPipeline


def test_audit_logger_tracking():
    """Verify AuditLogger records entries with transformations and warnings."""
    logger = AuditLogger()
    trans = TransformationRecord(
        field_name="gender",
        original_value="M",
        normalized_value="MALE",
        reason="Unambiguous mapping",
    )
    entry = logger.log(
        source_file="test.json",
        status=ProcessingStatus.NORMALIZED_WITH_WARNINGS,
        sanitized_mrn="MRN-101",
        facility_id="HOSP_A",
        transformations=[trans],
        warnings=["Ambiguous date defaulted to MM/DD/YYYY"],
    )
    assert entry.status == ProcessingStatus.NORMALIZED_WITH_WARNINGS
    assert len(entry.transformations) == 1
    assert len(entry.warnings) == 1
    assert entry.sanitized_mrn == "MRN-101"

    dict_list = logger.to_dict_list()
    assert len(dict_list) == 1
    assert dict_list[0]["sanitized_mrn"] == "MRN-101"


def test_pipeline_ingest_content_success_and_duplicate():
    """Verify IngestionPipeline processes valid records and handles exact duplicates."""
    pipeline = IngestionPipeline()
    payload = [
        {
            "mrn": "MRN-901",
            "first_name": "Diana",
            "last_name": "Prince",
            "date_of_birth": "1988-03-20",
            "gender": "female",
            "encounter_id": "ENC-01",
            "document_type": "CLINICAL_NOTE",
            "effective_date": "2024-01-15T10:00:00Z",
            "text_content": "Annual health checkup. All vitals normal.",
        },
        # Exact duplicate of the first record
        {
            "mrn": "MRN-901",
            "first_name": "Diana",
            "last_name": "Prince",
            "date_of_birth": "1988-03-20",
            "gender": "female",
            "encounter_id": "ENC-01",
            "document_type": "CLINICAL_NOTE",
            "effective_date": "2024-01-15T10:00:00Z",
            "text_content": "Annual health checkup. All vitals normal.",
        },
    ]

    result = pipeline.ingest_content(json.dumps(payload), "test_batch.json")
    assert result.total_processed == 2
    assert len(result.successful_records) == 1
    assert result.duplicate_count == 1
    assert len(result.audit_logs) == 2

    # Verify audit statuses
    statuses = [log.status for log in result.audit_logs]
    assert ProcessingStatus.SUCCESS in statuses
    assert ProcessingStatus.DUPLICATE_IGNORED in statuses


def test_pipeline_partial_failure_and_conflicts():
    """Verify IngestionPipeline isolates invalid records and detects identity conflicts."""
    pipeline = IngestionPipeline()
    payload = [
        # Record 1: Valid
        {
            "mrn": "MRN-888",
            "first_name": "Bruce",
            "last_name": "Wayne",
            "date_of_birth": "1979-05-27",
            "gender": "male",
            "effective_date": "2024-02-01T12:00:00Z",
            "text_content": "Mild concussion after night incident.",
        },
        # Record 2: Invalid (Missing required DOB)
        {
            "mrn": "MRN-999",
            "first_name": "Clark",
            "last_name": "Kent",
            "text_content": "Routine physical exam.",
        },
        # Record 3: Conflicting MRN (same MRN as Bruce Wayne, but different name/DOB)
        {
            "mrn": "MRN-888",
            "first_name": "Barry",
            "last_name": "Allen",
            "date_of_birth": "1992-09-30",
            "text_content": "Lab check for elevated metabolism.",
        },
    ]

    result = pipeline.ingest_content(json.dumps(payload), "mixed.json")
    assert result.total_processed == 3
    assert len(result.successful_records) == 1
    assert result.successful_records[0].patient.first_name == "Bruce"

    assert len(result.quarantined_records) == 2
    quarantine_reasons = [q.reason for q in result.quarantined_records]
    assert any("Missing required patient birth date" in r for r in quarantine_reasons)
    assert any("Identity conflict detected" in r for r in quarantine_reasons)

    audit_statuses = [log.status for log in result.audit_logs]
    assert ProcessingStatus.SUCCESS in audit_statuses
    assert ProcessingStatus.VALIDATION_FAILED in audit_statuses
    assert ProcessingStatus.CONFLICT_DETECTED in audit_statuses


def test_pipeline_quarantines_unassigned_plain_text():
    """Verify IngestionPipeline quarantines plain text note lacking patient identity."""
    pipeline = IngestionPipeline()
    unassigned_text = """CLINICAL SUMMARY:
Patient presented with headache. Prescribed ibuprofen. Follow up in 2 weeks.
"""
    result = pipeline.ingest_content(unassigned_text, "unassigned_note.txt")
    assert result.total_processed == 1
    assert len(result.successful_records) == 0
    assert len(result.quarantined_records) == 1
    assert "Unassigned clinical note" in result.quarantined_records[0].reason
    assert result.audit_logs[0].status == ProcessingStatus.QUARANTINED
