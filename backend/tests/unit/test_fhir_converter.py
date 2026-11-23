"""Unit tests for Stage 3: FHIRConverter (R4B resources & Bundles)."""
import base64
from datetime import date, datetime, timezone
import pytest

from app.fhir.converter import FHIRConverter
from app.schemas.enums import DocumentType, Gender
from app.schemas.intermediate import (
    ClinicalDocumentRecord,
    IntermediateRecord,
    PatientDemographics,
    SourceMetadata,
)


def _build_patient_demo():
    return PatientDemographics(
        mrn="MRN-8821",
        facility_id="GENERAL-HOSP",
        first_name="Arthur",
        last_name="Dent",
        date_of_birth=date(1978, 3, 11),
        gender=Gender.MALE,
        national_id="NID-992384",
        phone="555-0142",
        email="arthur@example.org",
        address="42 Country Lane, Cottington",
    )


def test_convert_patient_mapping():
    """Verify Patient conversion adheres to requirements: ID format, urn:example:national-id."""
    converter = FHIRConverter()
    demo = _build_patient_demo()
    patient = converter.convert_patient(demo, "REC-12345")

    # Requirement #10: pat-{record_id}
    assert patient.id == "pat-REC-12345"
    assert patient.gender == "male"
    assert str(patient.birthDate) == "1978-03-11"
    assert patient.name[0].family == "Dent"
    assert patient.name[0].given == ["Arthur"]

    # Verify identifiers: MRN and national ID
    systems = {i.system: i.value for i in patient.identifier}
    assert "http://hospital.smarthealth.org/GENERAL-HOSP/mrn" in systems
    assert systems["http://hospital.smarthealth.org/GENERAL-HOSP/mrn"] == "MRN-8821"

    # Requirement #6: urn:example:national-id
    assert "urn:example:national-id" in systems
    assert systems["urn:example:national-id"] == "NID-992384"

    # Telecom and Address
    assert patient.telecom[0].value == "555-0142"
    assert patient.telecom[1].value == "arthur@example.org"
    assert patient.address[0].text == "42 Country Lane, Cottington"


def test_convert_encounter_requirements():
    """Verify Encounter has status='unknown', class code='UNK', and subject reference."""
    converter = FHIRConverter()
    patient_ref_str = "urn:uuid:11111111-2222-3333-4444-555555555555"
    from fhir.resources.R4B.reference import Reference

    patient_ref = Reference(reference=patient_ref_str)
    encounter = converter.convert_encounter("ENC-99", patient_ref)

    assert encounter.id == "enc-ENC-99"
    # Requirement #4: status='unknown' and class 'UNK'
    assert encounter.status == "unknown"
    assert encounter.class_fhir.code == "UNK"
    assert encounter.class_fhir.system == "http://terminology.hl7.org/CodeSystem/v3-ActCode"
    assert encounter.subject.reference == patient_ref_str


def test_convert_document_reference_narrative_and_dates():
    """Verify DocumentReference embeds base64 narrative and omits date when null."""
    converter = FHIRConverter()
    from fhir.resources.R4B.reference import Reference
    patient_ref = Reference(reference="urn:uuid:pat-uuid")

    doc_with_date = ClinicalDocumentRecord(
        document_id="DOC-01",
        encounter_id="ENC-01",
        document_type=DocumentType.CLINICAL_NOTE,
        effective_date=datetime(2024, 2, 1, 10, 0, tzinfo=timezone.utc),
        title="Clinical Encounter Progress Note",
        text_content="Patient reports mild headache. Vitals normal.",
        content_hash="hash01",
    )
    doc_ref = converter.convert_document_reference(doc_with_date, patient_ref)

    assert doc_ref.id == "docref-DOC-01"
    assert doc_ref.status == "current"
    assert doc_ref.date is not None

    # Attachment data in fhir.resources is stored as bytes, and serializes to base64 in JSON
    raw_bytes = doc_ref.content[0].attachment.data
    assert raw_bytes.decode("utf-8") == "Patient reports mild headache. Vitals normal."
    # Verify in JSON serialization it is valid base64 (using model_dump_json)
    doc_json = doc_ref.model_dump_json()
    assert '"data":"' in doc_json

    # Document with null date (Requirement #3)
    doc_undated = ClinicalDocumentRecord(
        document_id="DOC-02",
        encounter_id="ENC-01",
        document_type=DocumentType.CLINICAL_NOTE,
        effective_date=None,
        title="Undated Clinical Note",
        text_content="Follow-up note with unspecified date.",
        content_hash="hash02",
    )
    doc_ref_undated = converter.convert_document_reference(doc_undated, patient_ref)
    assert doc_ref_undated.date is None  # Omitted, not substituted!


def test_convert_diagnostic_report_categories():
    """Verify DiagnosticReport for LAB_REPORT (LAB) and IMAGING (RAD)."""
    converter = FHIRConverter()
    from fhir.resources.R4B.reference import Reference
    patient_ref = Reference(reference="urn:uuid:pat-uuid")

    lab_doc = ClinicalDocumentRecord(
        document_id="DOC-LAB",
        document_type=DocumentType.LAB_REPORT,
        effective_date=datetime(2024, 2, 2, 9, 0, tzinfo=timezone.utc),
        title="Laboratory Diagnostic Report",
        text_content="WBC: 6.5, RBC: 4.8",
        diagnoses=["Normal blood count"],
        content_hash="hashlab",
    )
    lab_report = converter.convert_diagnostic_report(lab_doc, patient_ref)
    assert lab_report.id == "diagrep-DOC-LAB"
    assert lab_report.status == "final"
    assert lab_report.category[0].coding[0].code == "LAB"
    assert lab_report.conclusion == "Normal blood count"

    img_doc = ClinicalDocumentRecord(
        document_id="DOC-IMG",
        document_type=DocumentType.IMAGING,
        effective_date=None,
        title="Chest Radiograph",
        text_content="Lungs clear, no infiltrates.",
        content_hash="hashimg",
    )
    img_report = converter.convert_diagnostic_report(img_doc, patient_ref)
    assert img_report.category[0].coding[0].code == "RAD"
    assert img_report.effectiveDateTime is None  # Omitted when null


def test_bundle_referential_consistency():
    """Verify Bundle uses absolute urn:uuid fullUrls and consistent references."""
    converter = FHIRConverter()
    demo = _build_patient_demo()
    doc1 = ClinicalDocumentRecord(
        document_id="D1",
        encounter_id="E1",
        document_type=DocumentType.CLINICAL_NOTE,
        effective_date=datetime(2024, 1, 10, tzinfo=timezone.utc),
        title="Progress Note",
        text_content="Note text 1",
        content_hash="h1",
    )
    doc2 = ClinicalDocumentRecord(
        document_id="D2",
        encounter_id="E1",  # Same encounter shared
        document_type=DocumentType.LAB_REPORT,
        effective_date=datetime(2024, 1, 10, tzinfo=timezone.utc),
        title="Lab Report",
        text_content="Lab text 2",
        content_hash="h2",
    )
    record = IntermediateRecord(
        record_id="REC-TEST-BUNDLE",
        patient=demo,
        documents=[doc1, doc2],
        metadata=SourceMetadata(source_file="test.json", source_format="JSON"),
    )

    bundle = converter.convert_record_to_bundle(record)
    assert bundle.type == "collection"
    assert bundle.id == "bundle-REC-TEST-BUNDLE"

    # Total entries: 1 Patient + 1 Encounter (shared E1) + 1 DocRef + 1 DiagReport = 4
    assert len(bundle.entry) == 4
    assert bundle.total is None  # Collection bundles omit total per FHIR R4 spec

    # Requirement #7: All fullUrl values start with "urn:uuid:"
    full_urls = [entry.fullUrl for entry in bundle.entry]
    assert all(url.startswith("urn:uuid:") for url in full_urls)

    patient_entry = bundle.entry[0]
    patient_urn = patient_entry.fullUrl

    # Check that Encounter, DocumentReference, and DiagnosticReport strictly reference patient_urn
    for entry in bundle.entry[1:]:
        res = entry.resource
        assert res.subject.reference == patient_urn, f"Resource {res.id} has mismatched subject: {res.subject.reference}"
        # Ensure subject reference is strictly urn:uuid and NOT 'Patient/...'
        assert res.subject.reference.startswith("urn:uuid:")
        assert not res.subject.reference.startswith("Patient/")


def test_convert_document_without_encounter_id():
    """Verify that a document with no encounter_id produces no Encounter and no encounter reference."""
    converter = FHIRConverter()
    demo = _build_patient_demo()
    doc_no_enc = ClinicalDocumentRecord(
        document_id="D-NO-ENC",
        encounter_id=None,  # No encounter
        document_type=DocumentType.CLINICAL_NOTE,
        effective_date=datetime(2024, 1, 10, tzinfo=timezone.utc),
        title="Unscheduled Walk-in Note",
        text_content="Patient stopped by with general question.",
        content_hash="h_no_enc",
    )
    record = IntermediateRecord(
        record_id="REC-NO-ENC",
        patient=demo,
        documents=[doc_no_enc],
        metadata=SourceMetadata(source_file="test.json", source_format="JSON"),
    )

    bundle = converter.convert_record_to_bundle(record)
    # Total entries: 1 Patient + 1 DocumentReference = 2 (NO Encounter)
    assert len(bundle.entry) == 2
    resource_types = [e.resource.__class__.__name__ for e in bundle.entry]
    assert "Encounter" not in resource_types
    assert "DocumentReference" in resource_types
    assert "Patient" in resource_types

    # Verify DocumentReference has no context or encounter reference
    doc_ref = next(e.resource for e in bundle.entry if e.resource.__class__.__name__ == "DocumentReference")
    assert doc_ref.context is None
