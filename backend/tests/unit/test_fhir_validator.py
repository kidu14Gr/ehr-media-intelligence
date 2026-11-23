"""Unit tests for Stage 4: FHIRValidator (reference checks & integrity)."""
from datetime import date, datetime, timezone
import pytest
from fhir.resources.R4B.reference import Reference

from app.fhir.converter import FHIRConverter
from app.fhir.validator import FHIRValidator
from app.schemas.enums import DocumentType, Gender
from app.schemas.intermediate import (
    ClinicalDocumentRecord,
    IntermediateRecord,
    PatientDemographics,
    SourceMetadata,
)


def _build_valid_intermediate_record():
    patient = PatientDemographics(
        mrn="MRN-4001",
        facility_id="CENTRAL-HOSP",
        first_name="Arthur",
        last_name="Dent",
        date_of_birth=date(1975, 4, 15),
        gender=Gender.MALE,
    )
    doc = ClinicalDocumentRecord(
        document_id="DOC-CENTRAL-01",
        encounter_id="ENC-CENTRAL-01",
        document_type=DocumentType.CLINICAL_NOTE,
        effective_date=datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc),
        title="Central Clinic Note",
        text_content="Patient reports no chest pain or shortness of breath.",
        content_hash="hash_central",
    )
    return IntermediateRecord(
        record_id="REC-CENTRAL-01",
        patient=patient,
        documents=[doc],
        metadata=SourceMetadata(source_file="central.json", source_format="JSON"),
    )


def test_validate_bundle_valid():
    """Verify that a properly constructed Bundle passes validation with no errors."""
    converter = FHIRConverter()
    validator = FHIRValidator()
    record = _build_valid_intermediate_record()

    bundle = converter.convert_record_to_bundle(record)
    issues = validator.validate_bundle(bundle)

    error_issues = [i for i in issues if i.severity == "ERROR"]
    assert len(error_issues) == 0


def test_validate_bundle_unresolved_patient_reference():
    """Verify that an unresolvable patient reference is detected and flagged as ERROR."""
    converter = FHIRConverter()
    validator = FHIRValidator()
    record = _build_valid_intermediate_record()

    bundle = converter.convert_record_to_bundle(record)

    # Break the subject reference of the DocumentReference entry
    for entry in bundle.entry:
        if entry.resource.__class__.__name__ == "DocumentReference":
            entry.resource.subject = Reference(reference="urn:uuid:broken-patient-urn")

    issues = validator.validate_bundle(bundle)
    error_issues = [i for i in issues if i.severity == "ERROR"]

    assert len(error_issues) > 0
    assert any("broken-patient-urn" in i.message for i in error_issues)
    assert any(i.field_path == "subject.reference" for i in error_issues)


def test_validate_bundle_unresolved_encounter_reference():
    """Verify that an unresolvable encounter reference is detected as ERROR."""
    converter = FHIRConverter()
    validator = FHIRValidator()
    record = _build_valid_intermediate_record()

    bundle = converter.convert_record_to_bundle(record)

    # Break encounter reference
    for entry in bundle.entry:
        if entry.resource.__class__.__name__ == "DocumentReference":
            entry.resource.context.encounter[0] = Reference(reference="urn:uuid:phantom-encounter")

    issues = validator.validate_bundle(bundle)
    error_issues = [i for i in issues if i.severity == "ERROR"]

    assert len(error_issues) > 0
    assert any("phantom-encounter" in i.message for i in error_issues)


def test_validate_bundle_missing_patient():
    """Verify that a bundle missing a Patient resource is flagged as ERROR."""
    converter = FHIRConverter()
    validator = FHIRValidator()
    record = _build_valid_intermediate_record()

    bundle = converter.convert_record_to_bundle(record)
    # Remove Patient entry
    bundle.entry = [e for e in bundle.entry if e.resource.__class__.__name__ != "Patient"]

    issues = validator.validate_bundle(bundle)
    error_issues = [i for i in issues if i.severity == "ERROR"]
    assert any("does not contain a Patient resource" in i.message for i in error_issues)


def test_validate_bundles_consolidated_report():
    """Verify consolidated validation report aggregates counts and disclaimer note."""
    converter = FHIRConverter()
    validator = FHIRValidator()
    record = _build_valid_intermediate_record()

    bundle = converter.convert_record_to_bundle(record)
    report = validator.validate_bundles([bundle])

    assert report.is_valid is True
    assert report.total_bundles == 1
    assert report.total_resources == 3  # 1 Patient, 1 Encounter, 1 DocumentReference
    assert report.resource_counts["Patient"] == 1
    assert report.resource_counts["Encounter"] == 1
    assert report.resource_counts["DocumentReference"] == 1
    assert "Structural validation performed against HL7 FHIR R4B" in report.compliance_note
