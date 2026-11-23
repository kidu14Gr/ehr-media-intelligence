"""Integration tests for Task 3-5 quality improvements under v1.3.

Covers:
1. Chief concern extraction and anti-hallucination verification for Maria Garcia and David Kim.
2. Absence of system IDs and raw timestamps in summaries and search snippets.
3. MIN_RELEVANCE_SCORE calibration: 'chronic migraine' isolates Eleanor Vane and excludes
   Robert Neville and James Wilson, while 'lumbar spine MRI', 'chest pain', and 'hypertension'
   properly match their corresponding cohort patients.
4. GET /patients/{id} linked resources date behavior (Patient and Encounter).
"""
import re
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import get_settings
from app.ai.summarizer import ClinicalSummarizer
from app.search.vector_store import VectorStoreManager
from app.search.embeddings import get_embedding_client
from app.search.indexer import ClinicalIndexer
from app.search.service import ClinicalSearchService


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def settings():
    return get_settings()


def test_david_kim_and_maria_garcia_summaries():
    """Test David Kim has follow-up lab visit concern and no diagnoses, and Maria Garcia has imaging concern."""
    summarizer = ClinicalSummarizer()
    
    # David Kim
    david_summary = summarizer.summarize_patient("pat-REC-3703B61FE28A", force_refresh=False)
    assert david_summary.chief_concern is not None
    assert "lab" in david_summary.chief_concern.lower() or "follow-up" in david_summary.chief_concern.lower()
    assert len(david_summary.documented_diagnoses) == 0
    assert len(david_summary.unverified_or_flagged_diagnoses) == 0

    # Maria Garcia
    maria_summary = summarizer.summarize_patient("pat-REC-E20E0C1F7D1A", force_refresh=False)
    assert maria_summary.chief_concern is not None
    assert "mri" in maria_summary.chief_concern.lower() or "lumbar" in maria_summary.chief_concern.lower()


def test_no_system_ids_or_raw_timestamps_in_summaries():
    """Verify all cached summaries are strictly sanitized of system IDs and raw timestamps."""
    summarizer = ClinicalSummarizer()
    patient_ids = [
        "pat-REC-3703B61FE28A",
        "pat-REC-64A686188164",
        "pat-REC-C11B3AC92DB1",
        "pat-REC-C76B205792AB",
        "pat-REC-E20E0C1F7D1A",
    ]

    id_pattern = re.compile(r"\b(?:pat|docref|diagrep|enc|doc)-[A-Za-z0-9_-]+\b", re.IGNORECASE)
    raw_time_pattern = re.compile(r"\b\d{2}:\d{2}:\d{2}\b")
    mrn_pattern = re.compile(r"\bMRN[:\s-]*[A-Za-z0-9_-]+\b", re.IGNORECASE)

    for pid in patient_ids:
        summary = summarizer.summarize_patient(pid, force_refresh=False)
        narrative = summary.narrative_summary
        
        # Check narrative
        assert not id_pattern.search(narrative), f"Found system ID in narrative for {pid}: {narrative}"
        assert not raw_time_pattern.search(narrative), f"Found raw timestamp in narrative for {pid}: {narrative}"
        assert not mrn_pattern.search(narrative), f"Found MRN in narrative for {pid}: {narrative}"

        # Check chief concern if present
        if summary.chief_concern:
            assert not id_pattern.search(summary.chief_concern)
            assert not raw_time_pattern.search(summary.chief_concern)
            assert not mrn_pattern.search(summary.chief_concern)


def test_min_relevance_score_threshold_filters_chronic_migraine(client):
    """Verify MIN_RELEVANCE_SCORE=0.30 filters out irrelevant patients for 'chronic migraine'."""
    response = client.post("/search", json={"query": "chronic migraine"})
    assert response.status_code == 200
    data = response.json()
    
    returned_patients = [r["patient_name"] for r in data["results"]]
    
    # Eleanor Vane should be the primary match
    assert "Eleanor Vane" in returned_patients
    
    # Robert Neville and James Wilson should NOT be present (filtered by 0.30 threshold)
    assert "Robert Neville" not in returned_patients
    assert "James Wilson" not in returned_patients

    # Check scores
    for r in data["results"]:
        assert r["score"] >= 0.30


def test_core_queries_return_correct_cohort_patients(client):
    """Verify core clinical queries return their respective cohort patients above 0.30 threshold."""
    # 1. lumbar spine MRI -> Maria Garcia
    res_mri = client.post("/search", json={"query": "lumbar spine MRI"})
    assert res_mri.status_code == 200
    mri_patients = [r["patient_name"] for r in res_mri.json()["results"]]
    assert len(mri_patients) > 0
    assert mri_patients[0] == "Maria Garcia"

    # 2. chest pain -> James Wilson
    res_chest = client.post("/search", json={"query": "chest pain"})
    assert res_chest.status_code == 200
    chest_patients = [r["patient_name"] for r in res_chest.json()["results"]]
    assert len(chest_patients) > 0
    assert "James Wilson" in chest_patients

    # 3. hypertension -> Robert Neville
    res_htn = client.post("/search", json={"query": "hypertension"})
    assert res_htn.status_code == 200
    htn_patients = [r["patient_name"] for r in res_htn.json()["results"]]
    assert len(htn_patients) > 0
    assert "Robert Neville" in htn_patients


def test_search_snippets_clean_of_system_ids_and_timestamps(client):
    """Verify search snippets contain no system IDs or raw timestamps."""
    response = client.post("/search", json={"query": "lumbar spine MRI"})
    assert response.status_code == 200
    
    id_pattern = re.compile(r"\b(?:pat|docref|diagrep|enc|doc)-[A-Za-z0-9_-]+\b", re.IGNORECASE)
    raw_time_pattern = re.compile(r"\b\d{2}:\d{2}:\d{2}\b")

    for r in response.json()["results"]:
        snippet = r.get("summary_snippet", "")
        assert not id_pattern.search(snippet), f"Snippet contains system ID: {snippet}"
        assert not raw_time_pattern.search(snippet), f"Snippet contains raw timestamp: {snippet}"


def test_patient_drawer_linked_resources_structure(client):
    """Verify GET /patients/{id} returns linked resources with valid structures for UI display."""
    response = client.get("/patients/pat-REC-3703B61FE28A")
    assert response.status_code == 200
    data = response.json()
    
    linked = data.get("linked_resources", [])
    assert len(linked) > 0
    
    # Check that Patient and Encounter resource types exist
    types = [r["resource_type"] for r in linked]
    assert "Patient" in types
    assert "Encounter" in types
    assert "DocumentReference" in types
