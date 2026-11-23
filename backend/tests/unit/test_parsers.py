"""Unit tests for Stage 4: Multi-format parsers (JSON, CSV, Plain-text)."""
import json
import pytest
from app.ingestion.parsers import (
    JSONParser,
    CSVParser,
    PlainTextParser,
    get_parser_for_file,
)


def test_json_parser_single_object():
    """Verify JSONParser parses a single flat patient/document object."""
    payload = {
        "patient_id": "MRN-101",
        "given_name": "Alice",
        "family_name": "Smith",
        "dob": "1990-05-12",
        "gender": "female",
        "encounter_id": "ENC-100",
        "doc_type": "CLINICAL_NOTE",
        "date": "2024-01-15T09:00:00Z",
        "clinical_note": "Routine follow up for hypertension.",
    }
    parser = JSONParser()
    records = parser.parse(json.dumps(payload), "test.json")
    assert len(records) == 1
    rec = records[0]
    assert rec.patient_data["mrn"] == "MRN-101"
    assert rec.patient_data["first_name"] == "Alice"
    assert rec.patient_data["last_name"] == "Smith"
    assert rec.document_data["encounter_id"] == "ENC-100"
    assert rec.document_data["document_type"] == "CLINICAL_NOTE"
    assert "Routine follow up" in rec.document_data["text_content"]


def test_json_parser_nested_documents():
    """Verify JSONParser parses nested patient with document list."""
    payload = {
        "patient": {
            "mrn": "MRN-202",
            "first_name": "Bob",
            "last_name": "Jones",
            "date_of_birth": "1975-11-20",
        },
        "documents": [
            {
                "document_id": "DOC-1",
                "encounter_id": "ENC-01",
                "document_type": "LAB_REPORT",
                "effective_date": "2023-10-01T10:00:00Z",
                "text_content": "A1C: 6.2%",
            },
            {
                "document_id": "DOC-2",
                "encounter_id": "ENC-02",
                "document_type": "CLINICAL_NOTE",
                "effective_date": "2023-11-01T10:00:00Z",
                "text_content": "Follow-up note.",
            },
        ],
    }
    parser = JSONParser()
    records = parser.parse(json.dumps(payload), "test_nested.json")
    assert len(records) == 2
    assert records[0].patient_data["mrn"] == "MRN-202"
    assert records[0].document_data["document_type"] == "LAB_REPORT"
    assert records[1].document_data["document_type"] == "CLINICAL_NOTE"


def test_csv_parser_with_aliases_and_delimiters():
    """Verify CSVParser handles column aliases, whitespace, and tabs."""
    tsv_content = (
        "Patient_MRN\tFirst_Name\tLast_Name\tBirthDate\tGender\tVisit_ID\tNote_Type\tDate\tNarrative\n"
        "MRN-303\tCharlie\tBrown\t1982-03-14\tMale\tVISIT-88\tDISCHARGE_SUMMARY\t2024-02-01\tPatient discharged home.\n"
    )
    parser = CSVParser()
    records = parser.parse(tsv_content, "patients.tsv")
    assert len(records) == 1
    rec = records[0]
    assert rec.patient_data["mrn"] == "MRN-303"
    assert rec.patient_data["first_name"] == "Charlie"
    assert rec.document_data["encounter_id"] == "VISIT-88"
    assert rec.document_data["document_type"] == "DISCHARGE_SUMMARY"
    assert "discharged home" in rec.document_data["text_content"]


def test_plain_text_parser_with_headers():
    """Verify PlainTextParser extracts header metadata and preserves narrative."""
    text_content = """Patient Name: Sarah Connor
MRN: MRN-9099
DOB: 1984-02-28
Gender: Female
Facility: Metro General
Encounter ID: ENC-990
Document Type: CLINICAL_NOTE
Date: 2024-02-15 11:30:00

CHIEF COMPLAINT:
Patient presents with left ankle pain following a fall.

PHYSICAL EXAMINATION:
Mild swelling over lateral malleolus, no gross deformity.
"""
    parser = PlainTextParser()
    records = parser.parse(text_content, "note_01.txt")
    assert len(records) == 1
    rec = records[0]
    assert rec.is_unassigned is False
    assert rec.patient_data["mrn"] == "MRN-9099"
    assert rec.patient_data["first_name"] == "Sarah"
    assert rec.patient_data["last_name"] == "Connor"
    assert rec.patient_data["facility_id"] == "Metro General"
    assert rec.document_data["encounter_id"] == "ENC-990"
    assert "Mild swelling over lateral malleolus" in rec.document_data["text_content"]


def test_plain_text_parser_unassigned_document():
    """Verify plain text without identifiable patient metadata is flagged as unassigned."""
    unassigned_text = """OPERATIVE NOTE
PROCEDURE: Appendectomy
SURGEON: Dr. Miller
The appendix was identified, mobilized, and resected without complication.
"""
    parser = PlainTextParser()
    records = parser.parse(unassigned_text, "op_note.txt")
    assert len(records) == 1
    rec = records[0]
    assert rec.is_unassigned is True
    assert "Unassigned clinical note" in rec.unassigned_reason
    assert "Appendectomy" in rec.document_data["text_content"]


def test_parser_factory_selection():
    """Verify get_parser_for_file selects correct parser."""
    assert isinstance(get_parser_for_file("data.json"), JSONParser)
    assert isinstance(get_parser_for_file("records.csv"), CSVParser)
    assert isinstance(get_parser_for_file("records.tsv"), CSVParser)
    assert isinstance(get_parser_for_file("note.txt"), PlainTextParser)
