"""Unit tests for Stage 5: DualFHIRStorage and idempotent SQLite upserts."""
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3
import pytest

from app.fhir.converter import FHIRConverter
from app.fhir.storage import BaseFHIRStorage, DualFHIRStorage
from app.schemas.enums import DocumentType, Gender
from app.schemas.fhir_report import FHIRValidationReport
from app.schemas.intermediate import (
    ClinicalDocumentRecord,
    IntermediateRecord,
    PatientDemographics,
    SourceMetadata,
)


@pytest.fixture
def temp_storage(tmp_path: Path) -> DualFHIRStorage:
    json_dir = tmp_path / "fhir_json"
    db_path = tmp_path / "test_clinical.db"
    return DualFHIRStorage(json_dir=json_dir, db_path=db_path)


def _build_test_record(record_id: str, mrn: str) -> IntermediateRecord:
    patient = PatientDemographics(
        mrn=mrn,
        facility_id="TEST-FAC",
        first_name="Ford",
        last_name="Prefect",
        date_of_birth=date(1979, 7, 20),
        gender=Gender.MALE,
    )
    doc = ClinicalDocumentRecord(
        document_id=f"DOC-{mrn}-1",
        encounter_id=f"ENC-{mrn}-01",
        document_type=DocumentType.CLINICAL_NOTE,
        effective_date=datetime(2024, 1, 10, tzinfo=timezone.utc),
        title="Encounter Note",
        text_content="Clinical examination notes for Ford Prefect.",
        content_hash=f"hash-{mrn}",
    )
    return IntermediateRecord(
        record_id=record_id,
        patient=patient,
        documents=[doc],
        metadata=SourceMetadata(source_file="test.json", source_format="JSON"),
    )


def test_abstract_interface_conformance():
    """Verify DualFHIRStorage is a concrete subclass of BaseFHIRStorage."""
    assert issubclass(DualFHIRStorage, BaseFHIRStorage)


def test_storage_idempotent_upsert(temp_storage: DualFHIRStorage):
    """Verify storing bundles multiple times is idempotent and row counts do not change."""
    converter = FHIRConverter()
    rec1 = _build_test_record("REC-01", "MRN-101")
    rec2 = _build_test_record("REC-02", "MRN-102")

    bundles = converter.convert_records([rec1, rec2])

    # First run of storage
    temp_storage.store_bundles(bundles)

    def get_row_counts():
        with sqlite3.connect(temp_storage.db_path) as conn:
            cursor = conn.cursor()
            patients_count = cursor.execute("SELECT count(*) FROM patients;").fetchone()[0]
            resources_count = cursor.execute("SELECT count(*) FROM fhir_resources;").fetchone()[0]
            bundles_count = cursor.execute("SELECT count(*) FROM fhir_bundles;").fetchone()[0]
            return patients_count, resources_count, bundles_count

    patients_1, resources_1, bundles_1 = get_row_counts()
    assert patients_1 == 2
    assert resources_1 == 6  # Each patient has 1 Patient + 1 Encounter + 1 DocumentReference = 3 * 2 = 6
    assert bundles_1 == 2

    # Second run of storage with identical bundles
    temp_storage.store_bundles(bundles)

    patients_2, resources_2, bundles_2 = get_row_counts()
    assert patients_2 == patients_1, "Patients row count changed after re-storing!"
    assert resources_2 == resources_1, "Resources row count changed after re-storing!"
    assert bundles_2 == bundles_1, "Bundles row count changed after re-storing!"


def test_storage_json_files_written(temp_storage: DualFHIRStorage):
    """Verify that individual and combined JSON bundles and reports are exported."""
    converter = FHIRConverter()
    rec = _build_test_record("REC-JSON", "MRN-JSON")
    bundles = converter.convert_records([rec])

    temp_storage.store_bundles(bundles)

    # Individual file check (clean bundle-<id>.json filename without doubled prefix)
    bundle_file = temp_storage.json_dir / f"{bundles[0].id}.json"
    assert bundle_file.exists()

    # Combined file check
    all_file = temp_storage.json_dir / "all_fhir_bundles.json"
    assert all_file.exists()

    # Validation report check
    report = FHIRValidationReport(
        is_valid=True,
        total_bundles=1,
        total_resources=3,
        resource_counts={"Patient": 1, "Encounter": 1, "DocumentReference": 1},
    )
    temp_storage.store_validation_report(report)
    report_file = temp_storage.json_dir / "fhir_validation_report.json"
    assert report_file.exists()


def test_get_bundle(temp_storage: DualFHIRStorage):
    """Verify retrieving a bundle by bundle_id."""
    converter = FHIRConverter()
    rec = _build_test_record("REC-GET", "MRN-GET")
    bundles = converter.convert_records([rec])

    temp_storage.store_bundles(bundles)
    retrieved = temp_storage.get_bundle(bundles[0].id)

    assert retrieved is not None
    assert retrieved["id"] == bundles[0].id
    assert retrieved["type"] == "collection"
