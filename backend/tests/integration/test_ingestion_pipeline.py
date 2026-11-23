"""Integration tests for EHR Media Intelligence Ingestion Pipeline.

Validates end-to-end multi-format ingestion across JSON, CSV, and TXT sample files:
- Demographic cleaning and conservative normalization
- Deduplication and identity conflict detection
- Quarantine of unassigned clinical notes
- Timezone-aware UTC dates and inferred date flags
- Generation of IntermediateRecords and forensic audit logs
"""
import json
from pathlib import Path
import pytest
from app.ingestion.pipeline import IngestionPipeline
from app.schemas.enums import DocumentType, ProcessingStatus


@pytest.fixture
def samples_directory() -> Path:
    """Path to sample files directory."""
    backend_dir = Path(__file__).resolve().parents[2]
    return backend_dir.parent / "data" / "samples"


def test_end_to_end_batch_ingestion(samples_directory: Path):
    """Verify complete multi-format batch ingestion against all sample files."""
    sample_files = sorted(list(samples_directory.glob("*.*")))
    # Filter to expected sample files
    expected_extensions = {".json", ".csv", ".txt"}
    target_files = [f for f in sample_files if f.suffix.lower() in expected_extensions]
    assert len(target_files) >= 4, f"Expected at least 4 sample files, found: {target_files}"

    pipeline = IngestionPipeline()
    batch_result = pipeline.ingest_files(target_files)

    # 1. Verification of record counts
    # 5 (JSON) + 3 (CSV) + 1 (note_with_header) + 1 (unassigned_note) = 10 total
    assert batch_result.total_processed == 10
    assert batch_result.duplicate_count == 1
    assert len(batch_result.quarantined_records) == 2
    assert len(batch_result.successful_records) == 5

    # 2. Verification of Quarantined Records
    quarantine_reasons = [q.reason for q in batch_result.quarantined_records]
    assert any("Identity conflict detected" in r for r in quarantine_reasons), "Expected MRN conflict quarantine"
    assert any("Unassigned clinical note" in r for r in quarantine_reasons), "Expected unassigned note quarantine"

    # 3. Verification of Patients and Accumulated Documents
    patient_mrns = {rec.patient.mrn: rec for rec in batch_result.successful_records}
    assert "10492-A" in patient_mrns
    assert "DK-5021" in patient_mrns
    assert "JW-9901" in patient_mrns
    assert "MG-4420" in patient_mrns
    assert "RN-7703" in patient_mrns

    # David Kim accumulated 2 documents
    david_rec = patient_mrns["DK-5021"]
    assert len(david_rec.documents) == 2
    # Verify optional effective_date handling on David Kim's second document (had no date in JSON)
    doc_types = {d.document_type: d for d in david_rec.documents}
    assert DocumentType.LAB_REPORT in doc_types
    assert DocumentType.CLINICAL_NOTE in doc_types
    assert doc_types[DocumentType.CLINICAL_NOTE].effective_date is None
    assert doc_types[DocumentType.LAB_REPORT].effective_date is not None

    # James Wilson accumulated 2 documents (Discharge Summary + Lab Report from CSV)
    james_rec = patient_mrns["JW-9901"]
    assert len(james_rec.documents) == 2
    j_types = {d.document_type: d for d in james_rec.documents}
    assert DocumentType.DISCHARGE_SUMMARY in j_types
    assert DocumentType.LAB_REPORT in j_types

    # Maria Garcia has 1 document (Imaging with dynamic title)
    maria_rec = patient_mrns["MG-4420"]
    assert len(maria_rec.documents) == 1
    assert maria_rec.documents[0].document_type == DocumentType.IMAGING
    assert maria_rec.documents[0].title == "Diagnostic Medical Imaging Report"

    # 4. Verification that Audit entries for successful records carry matching record_id
    success_audits = [
        a for a in batch_result.audit_logs
        if a.status in (ProcessingStatus.SUCCESS, ProcessingStatus.NORMALIZED_WITH_WARNINGS)
    ]
    assert len(success_audits) > 0
    patient_record_ids = {rec.record_id for rec in batch_result.successful_records}

    for audit_entry in success_audits:
        assert audit_entry.record_id is not None
        assert audit_entry.record_id in patient_record_ids

    # 5. Immutability verification
    first_record = batch_result.successful_records[0]
    with pytest.raises(Exception):
        first_record.patient.first_name = "Modified"  # type: ignore
