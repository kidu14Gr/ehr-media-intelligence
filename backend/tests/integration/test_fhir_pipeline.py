"""End-to-End integration tests for Task 2 FHIR R4B Normalization & Storage Pipeline.

Validates the full pipeline flow from cleaned intermediate records to validated FHIR R4B
Bundles and dual persistence in JSON and SQLite:
1. End-to-end ingestion -> conversion -> validation -> persistence.
2. Exclusion of quarantined records from FHIR conversion.
3. Strict referential integrity (urn:uuid fullUrls, no mixed references).
4. Omission of clinical dates for undated notes.
5. Idempotent SQLite upserts across multiple runs.
6. Verification that collection bundles omit the 'total' property per FHIR R4 spec.
"""
import base64
import json
from pathlib import Path
import sqlite3
import pytest

from app.fhir.converter import FHIRConverter
from app.fhir.storage import DualFHIRStorage
from app.fhir.validator import FHIRValidator
from app.ingestion.pipeline import IngestionPipeline
from app.schemas.enums import DocumentType, ProcessingStatus


@pytest.fixture
def samples_directory() -> Path:
    """Path to source sample files directory."""
    backend_dir = Path(__file__).resolve().parents[2]
    return backend_dir.parent / "data" / "samples"


def test_end_to_end_fhir_pipeline(samples_directory: Path, tmp_path: Path):
    """Test full integration from raw sample files to FHIR R4B bundles and dual storage."""
    # Step 1: Ingestion Pipeline (Task 1)
    sample_files = sorted(list(samples_directory.glob("*.*")))
    target_files = [f for f in sample_files if f.suffix.lower() in {".json", ".csv", ".txt"}]
    assert len(target_files) >= 4

    pipeline = IngestionPipeline()
    batch_result = pipeline.ingest_files(target_files)

    assert len(batch_result.successful_records) == 5
    assert len(batch_result.quarantined_records) == 2

    # Step 2: FHIR Conversion (Task 2) - only successful records are converted
    converter = FHIRConverter()
    bundles = converter.convert_records(batch_result.successful_records)

    assert len(bundles) == 5

    # Requirement: collection bundles omit total
    for bundle in bundles:
        assert bundle.type == "collection"
        assert bundle.total is None

    # Step 3: FHIR Validation
    validator = FHIRValidator()
    report = validator.validate_bundles(bundles)

    assert report.is_valid is True
    assert len(report.issues) == 0
    assert report.total_bundles == 5
    assert report.total_resources == 19
    assert report.resource_counts["Patient"] == 5
    assert report.resource_counts["Encounter"] == 7
    assert report.resource_counts["DocumentReference"] == 4
    assert report.resource_counts["DiagnosticReport"] == 3

    # Step 4: Dual Persistence
    json_dir = tmp_path / "fhir_out"
    db_path = tmp_path / "test_clinical.db"
    storage = DualFHIRStorage(json_dir=json_dir, db_path=db_path)

    storage.store_bundles(bundles)
    storage.store_validation_report(report)

    # Verify JSON persistence
    assert (json_dir / "all_fhir_bundles.json").exists()
    assert (json_dir / "fhir_validation_report.json").exists()
    for bundle in bundles:
        assert (json_dir / f"{bundle.id}.json").exists()
        # Verify no doubled bundle_ prefix
        assert not (json_dir / f"bundle_{bundle.id}.json").exists()

    # Verify SQLite database persistence and schema
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        patients_count = cursor.execute("SELECT count(*) FROM patients;").fetchone()[0]
        resources_count = cursor.execute("SELECT count(*) FROM fhir_resources;").fetchone()[0]
        bundles_count = cursor.execute("SELECT count(*) FROM fhir_bundles;").fetchone()[0]

        assert patients_count == 5
        assert resources_count == 19
        assert bundles_count == 5

        # Check breakdown by resource_type in SQLite
        type_rows = cursor.execute(
            "SELECT resource_type, count(*) FROM fhir_resources GROUP BY resource_type ORDER BY resource_type;"
        ).fetchall()
        type_counts = dict(type_rows)
        assert type_counts["DiagnosticReport"] == 3
        assert type_counts["DocumentReference"] == 4
        assert type_counts["Encounter"] == 7
        assert type_counts["Patient"] == 5


def test_quarantined_records_excluded_from_fhir_conversion(samples_directory: Path, tmp_path: Path):
    """Verify that quarantined records from Task 1 are strictly excluded from FHIR bundles and DB."""
    sample_files = sorted(list(samples_directory.glob("*.*")))
    target_files = [f for f in sample_files if f.suffix.lower() in {".json", ".csv", ".txt"}]

    pipeline = IngestionPipeline()
    batch_result = pipeline.ingest_files(target_files)

    # Collect quarantined record descriptions / sources
    quarantined = batch_result.quarantined_records
    assert len(quarantined) == 2

    converter = FHIRConverter()
    # Explicitly verify converter only receives and processes successful records
    bundles = converter.convert_records(batch_result.successful_records)

    json_dir = tmp_path / "fhir_quarantine"
    db_path = tmp_path / "quarantine_test.db"
    storage = DualFHIRStorage(json_dir=json_dir, db_path=db_path)
    storage.store_bundles(bundles)

    # Verify unassigned note content does NOT exist in stored FHIR resources
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        rows = cursor.execute("SELECT fhir_json FROM fhir_resources;").fetchall()
        all_json_text = " ".join(r[0] for r in rows)

        # Unassigned note text from unassigned_document.txt
        assert "Unassigned Note" not in all_json_text
        assert "No identifiable patient header" not in all_json_text

        # Verify only the 5 valid patient IDs exist in patients table
        patient_ids = [r[0] for r in cursor.execute("SELECT patient_id FROM patients;").fetchall()]
        assert len(patient_ids) == 5
        assert all(pid.startswith("pat-REC-") for pid in patient_ids)


def test_bundle_referential_integrity_and_undated_handling(samples_directory: Path):
    """Verify absolute urn:uuid fullUrls, consistent internal references, and undated handling."""
    sample_files = sorted(list(samples_directory.glob("*.*")))
    target_files = [f for f in sample_files if f.suffix.lower() in {".json", ".csv", ".txt"}]

    pipeline = IngestionPipeline()
    batch_result = pipeline.ingest_files(target_files)

    converter = FHIRConverter()
    bundles = converter.convert_records(batch_result.successful_records)

    for bundle in bundles:
        # 1. fullUrls must all be absolute urn:uuid:
        entry_urns = {entry.fullUrl for entry in bundle.entry}
        assert all(u.startswith("urn:uuid:") for u in entry_urns)

        # 2. First entry is always the Patient
        patient_entry = bundle.entry[0]
        patient_res = patient_entry.resource
        assert patient_res.get_resource_type() == "Patient"
        assert patient_res.id.startswith("pat-REC-")
        patient_urn = patient_entry.fullUrl

        # 3. Every clinical resource points strictly to patient_urn (never Patient/...)
        for entry in bundle.entry[1:]:
            res = entry.resource
            assert res.subject.reference == patient_urn
            assert not res.subject.reference.startswith("Patient/")

        # 4. Check encounters have unknown status and placeholder UNK class
        encounter_entries = [e for e in bundle.entry if e.resource.get_resource_type() == "Encounter"]
        for enc_entry in encounter_entries:
            enc = enc_entry.resource
            assert enc.status == "unknown"
            assert enc.class_fhir.code == "UNK"
            assert enc.class_fhir.system == "http://terminology.hl7.org/CodeSystem/v3-ActCode"

    # 5. Check David Kim's undated note
    david_rec = next(r for r in batch_result.successful_records if r.patient.last_name == "Kim")
    david_bundle = next(b for b in bundles if b.id == f"bundle-{david_rec.record_id}")

    doc_ref_entry = next(
        e for e in david_bundle.entry
        if e.resource.get_resource_type() == "DocumentReference"
    )
    doc_ref = doc_ref_entry.resource
    # Date must be omitted (None), NOT ingestion timestamp
    assert doc_ref.date is None
    # Base64 content check
    raw_content = doc_ref.content[0].attachment.data
    assert b"Follow-up visit discussing lab results" in raw_content


def test_storage_end_to_end_idempotency(samples_directory: Path, tmp_path: Path):
    """Verify that repeatedly running conversion and storage does not duplicate rows."""
    sample_files = sorted(list(samples_directory.glob("*.*")))
    target_files = [f for f in sample_files if f.suffix.lower() in {".json", ".csv", ".txt"}]

    pipeline = IngestionPipeline()
    batch_result = pipeline.ingest_files(target_files)
    bundles = FHIRConverter().convert_records(batch_result.successful_records)

    db_path = tmp_path / "idempotent_test.db"
    json_dir = tmp_path / "fhir_idempotent"
    storage = DualFHIRStorage(json_dir=json_dir, db_path=db_path)

    # First write
    storage.store_bundles(bundles)

    def get_counts():
        with sqlite3.connect(db_path) as conn:
            c = conn.cursor()
            p = c.execute("SELECT count(*) FROM patients;").fetchone()[0]
            r = c.execute("SELECT count(*) FROM fhir_resources;").fetchone()[0]
            b = c.execute("SELECT count(*) FROM fhir_bundles;").fetchone()[0]
            return p, r, b

    p1, r1, b1 = get_counts()
    assert p1 == 5
    assert r1 == 19
    assert b1 == 5

    # Second write
    storage.store_bundles(bundles)
    p2, r2, b2 = get_counts()
    assert (p1, r1, b1) == (p2, r2, b2)

    # Third write
    storage.store_bundles(bundles)
    p3, r3, b3 = get_counts()
    assert (p1, r1, b1) == (p3, r3, b3)
