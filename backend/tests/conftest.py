"""Shared Pytest fixtures for EHR Media Intelligence tests."""
import pytest
from datetime import date, datetime, timezone
from typing import Dict, Any


@pytest.fixture
def sample_patient_dict() -> Dict[str, Any]:
    """Sample raw patient dictionary for testing."""
    return {
        "mrn": "MRN-10023",
        "first_name": "John",
        "last_name": "Doe",
        "date_of_birth": "1980-05-15",
        "gender": "male",
        "facility_id": "HOSP_A",
        "phone": "555-0199",
        "email": "john.doe@example.com",
    }


@pytest.fixture
def sample_clinical_note_dict() -> Dict[str, Any]:
    """Sample raw clinical note dictionary for testing."""
    return {
        "document_id": "DOC-9001",
        "encounter_id": "ENC-4501",
        "document_type": "CLINICAL_NOTE",
        "effective_date": "2024-01-10T14:30:00Z",
        "title": "Outpatient Progress Note",
        "text_content": "Patient presents with persistent dry cough for 2 weeks. Denies fever. Vitals stable.",
        "author": "Dr. Sarah Lin, MD",
        "facility": "General Hospital",
    }
