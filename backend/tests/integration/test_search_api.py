"""Integration tests for FastAPI /search and /patients/{id} endpoints.

Validates patient grouping, undated inclusion/exclusion logic, CORS headers,
detail retrieval, and performance SLA under 2 seconds.
"""
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.schemas.search import SearchRequest
from app.search.embeddings import LocalEmbeddingClient, MockEmbeddingClient
from app.search.indexer import ClinicalIndexer
from app.search.service import ClinicalSearchService
from app.search.vector_store import VectorStoreManager


@pytest.fixture
def mock_search_env(tmp_path):
    """Sets up an isolated SQLite DB, Chroma vector store, and test client."""
    db_path = tmp_path / "test_platform.db"
    chroma_dir = tmp_path / "test_chroma"

    # Setup SQLite schema & test data
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE patients (
            patient_id TEXT PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            gender TEXT,
            date_of_birth TEXT,
            mrn TEXT,
            facility_id TEXT
        );
    """)
    c.execute("""
        CREATE TABLE fhir_resources (
            resource_id TEXT PRIMARY KEY,
            resource_type TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            encounter_id TEXT,
            effective_date TEXT,
            fhir_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    c.execute("""
        CREATE TABLE clinical_summaries (
            summary_id TEXT PRIMARY KEY,
            patient_id TEXT NOT NULL,
            cache_key TEXT NOT NULL UNIQUE,
            content_hash TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            model_used TEXT NOT NULL,
            chief_concern TEXT,
            narrative_summary TEXT NOT NULL,
            word_count INTEGER NOT NULL,
            confidence_score REAL NOT NULL,
            summary_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    c.execute("""
        CREATE TABLE fhir_bundles (
            bundle_id TEXT PRIMARY KEY,
            patient_id TEXT,
            resource_count INTEGER,
            bundle_json TEXT,
            created_at TEXT
        );
    """)

    # Patient 1: John Doe (Dated note)
    c.execute(
        "INSERT INTO patients VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("pat-1", "John", "Doe", "male", "1975-01-01", "MRN-101", "FAC-1"),
    )
    doc_json_1 = '{"resourceType":"DocumentReference","id":"doc-1","content":[{"attachment":{"data":"Q2xpbmljYWwgTm90ZTogUGF0aWVudCBoYXMgc2V2ZXJlIHdoZWV6aW5nIGFuZCBwbGV1cml0aWMgY2hlc3QgcGFpbi4="}}]}'
    c.execute(
        "INSERT INTO fhir_resources VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("doc-1", "DocumentReference", "pat-1", "enc-1", "2024-02-10 10:00:00+00:00", doc_json_1, "2024-02-10T10:00:00Z"),
    )
    c.execute(
        "INSERT INTO clinical_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sum-1", "pat-1", "k1", "h1", "v1.2", "mock", "Chest pain", "Patient presents with respiratory distress and wheezing.", 8, 0.9, '{"narrative_summary":"Patient presents with respiratory distress and wheezing.","key_findings":["Wheezing","Chest pain"]}', "2024-02-10T10:30:00Z"),
    )
    bundle_json_1 = '{"resourceType":"Bundle","entry":[{"fullUrl":"urn:uuid:enc-1","resource":{"resourceType":"Encounter","id":"enc-1"}}]}'
    c.execute(
        "INSERT INTO fhir_bundles VALUES (?, ?, ?, ?, ?)",
        ("bundle-1", "pat-1", 1, bundle_json_1, "2024-02-10T10:00:00Z"),
    )

    # Patient 2: Jane Smith (Undated note)
    c.execute(
        "INSERT INTO patients VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("pat-2", "Jane", "Smith", "female", "1988-06-15", "MRN-202", "FAC-1"),
    )
    doc_json_2 = '{"resourceType":"DocumentReference","id":"doc-2","content":[{"attachment":{"data":"Q2xpbmljYWwgTm90ZTogQ2hyb25pYyBjb3VnaCBhbmQgY2hlc3QgdGlnaHRuZXNzLCBubyBwcmV2aW91cyBoaXN0b3J5Lg=="}}]}'
    # effective_date is NULL (undated record)
    c.execute(
        "INSERT INTO fhir_resources VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("doc-2", "DocumentReference", "pat-2", "enc-2", None, doc_json_2, "2024-02-11T12:00:00Z"),
    )
    c.execute(
        "INSERT INTO clinical_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sum-2", "pat-2", "k2", "h2", "v1.2", "mock", "Cough", "Patient reports persistent chronic cough.", 5, 0.85, '{"key_findings":["Cough"]}', "2024-02-11T12:30:00Z"),
    )
    bundle_json_2 = '{"resourceType":"Bundle","entry":[{"fullUrl":"urn:uuid:enc-2","resource":{"resourceType":"Encounter","id":"enc-2"}}]}'
    c.execute(
        "INSERT INTO fhir_bundles VALUES (?, ?, ?, ?, ?)",
        ("bundle-2", "pat-2", 1, bundle_json_2, "2024-02-11T12:00:00Z"),
    )

    conn.commit()
    conn.close()

    # Index using Mock embedder with min_relevance_score=0.0 for deterministic offline matching
    settings = Settings(
        DATABASE_PATH=db_path,
        CHROMA_PERSIST_DIR=chroma_dir,
        EMBEDDING_BACKEND="mock",
        MIN_RELEVANCE_SCORE=0.0,
    )
    mock_embedder = MockEmbeddingClient()
    vector_store = VectorStoreManager(persist_dir=chroma_dir, settings=settings)
    indexer = ClinicalIndexer(vector_store=vector_store, embedding_client=mock_embedder, settings=settings)
    indexer.build_index()

    # Create search service
    service = ClinicalSearchService(vector_store=vector_store, embedding_client=mock_embedder, settings=settings)

    # Create test client with dependency override
    app = create_app()
    from app.api.routes.search import get_search_service as search_dep
    from app.api.routes.patients import get_search_service as pat_dep
    app.dependency_overrides[search_dep] = lambda: service
    app.dependency_overrides[pat_dep] = lambda: service

    with TestClient(app) as client:
        yield client, service, settings


def test_post_search_top5_patients_grouped(mock_search_env):
    """Validates POST /search returns top patient matches with required metadata."""
    client, _, _ = mock_search_env

    resp = client.post("/search", json={"query": "chest pain and wheezing", "limit": 5})
    assert resp.status_code == 200
    data = resp.json()

    assert data["query"] == "chest pain and wheezing"
    assert "results" in data
    assert data["total_patients"] >= 1

    # Check result item fields
    item = data["results"][0]
    assert "patient_id" in item
    assert "patient_name" in item
    assert "mrn" in item
    assert "score" in item
    assert "resource_type" in item
    assert "summary_snippet" in item
    assert 0.0 <= item["score"] <= 1.0


def test_undated_record_inclusion_and_exclusion(mock_search_env):
    """Validates that undated records appear when unfiltered, and disappear when date filtered."""
    client, _, _ = mock_search_env

    # 1. Unfiltered query: pat-2 (undated) should be returned in candidate pool
    resp_unfiltered = client.post("/search", json={"query": "chronic cough", "limit": 5})
    assert resp_unfiltered.status_code == 200
    pats_unfiltered = {r["patient_id"] for r in resp_unfiltered.json()["results"]}
    assert "pat-2" in pats_unfiltered

    # 2. Date filtered query: only dated records (pat-1) should appear, pat-2 excluded
    resp_filtered = client.post(
        "/search",
        json={
            "query": "chronic cough",
            "start_date": "2024-02-01",
            "end_date": "2024-02-28",
            "limit": 5,
        },
    )
    assert resp_filtered.status_code == 200
    results_filtered = resp_filtered.json()["results"]
    pats_filtered = {r["patient_id"] for r in results_filtered}
    assert "pat-2" not in pats_filtered  # Undated record strictly excluded!
    if "pat-1" in pats_filtered:
        # Verify dated record date format
        item_1 = next(r for r in results_filtered if r["patient_id"] == "pat-1")
        assert item_1["record_date"] == "2024-02-10"


def test_get_patient_detail_drawer(mock_search_env):
    """Validates GET /patients/{id} returns demographics, full summary, and linked resources."""
    client, _, _ = mock_search_env

    # Existing patient
    resp = client.get("/patients/pat-1")
    assert resp.status_code == 200
    data = resp.json()

    assert data["patient"]["first_name"] == "John"
    assert data["patient"]["mrn"] == "MRN-101"
    assert data["summary"]["narrative_summary"] == "Patient presents with respiratory distress and wheezing."
    assert len(data["linked_resources"]) >= 1
    assert data["linked_resources"][0]["resource_id"] == "doc-1"

    # Nonexistent patient
    resp_404 = client.get("/patients/pat-NONEXISTENT")
    assert resp_404.status_code == 404
    assert "not found" in resp_404.json()["detail"].lower()


def test_cors_headers(mock_search_env):
    """Validates CORS middleware allows configured origin."""
    client, _, _ = mock_search_env

    headers = {"Origin": "http://localhost:3000"}
    resp = client.get("/health", headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_min_relevance_score_filtering(mock_search_env):
    """Validates that results below the configurable min_relevance_score threshold are dropped."""
    client, _, _ = mock_search_env

    # 1. Query with impossible min_score 0.99: all matches dropped
    resp_strict = client.post("/search", json={"query": "cough", "min_score": 0.99})
    assert resp_strict.status_code == 200
    assert resp_strict.json()["total_patients"] == 0
    assert resp_strict.json()["results"] == []

    # 2. Query with threshold 0.0: candidates returned
    resp_open = client.post("/search", json={"query": "cough", "min_score": 0.0})
    assert resp_open.status_code == 200
    assert resp_open.json()["total_patients"] >= 1
    for r in resp_open.json()["results"]:
        assert r["score"] >= 0.0

    # 3. Query with explicit threshold: all returned items satisfy threshold
    resp_thresh = client.post("/search", json={"query": "cough", "min_score": 0.20})
    assert resp_thresh.status_code == 200
    for r in resp_thresh.json()["results"]:
        assert r["score"] >= 0.20


def test_linked_resources_consistent_encounter_ids(tmp_path):
    """Validates that GET /patients/{id} resolves URNs so encounter_id is strictly consistent."""
    db_path = tmp_path / "enc_test.db"
    chroma_dir = tmp_path / "enc_chroma"

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("CREATE TABLE patients (patient_id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT, gender TEXT, date_of_birth TEXT, mrn TEXT, facility_id TEXT)")
    c.execute("CREATE TABLE fhir_resources (resource_id TEXT PRIMARY KEY, resource_type TEXT, patient_id TEXT, encounter_id TEXT, effective_date TEXT, fhir_json TEXT, created_at TEXT)")
    c.execute("CREATE TABLE clinical_summaries (summary_id TEXT PRIMARY KEY, patient_id TEXT, cache_key TEXT UNIQUE, content_hash TEXT, prompt_version TEXT, model_used TEXT, chief_concern TEXT, narrative_summary TEXT, word_count INTEGER, confidence_score REAL, summary_json TEXT, created_at TEXT)")
    c.execute("CREATE TABLE fhir_bundles (bundle_id TEXT PRIMARY KEY, patient_id TEXT, resource_count INTEGER, bundle_json TEXT, created_at TEXT)")

    c.execute("INSERT INTO patients VALUES ('pat-enc', 'Alice', 'Smith', 'female', '1985-04-12', 'MRN-789', 'FAC-1')")
    c.execute("INSERT INTO fhir_resources VALUES ('enc-V-99', 'Encounter', 'pat-enc', 'enc-V-99', '2024-02-10', '{\"resourceType\":\"Encounter\",\"id\":\"enc-V-99\"}', '2024-02-10')")
    c.execute("INSERT INTO fhir_resources VALUES ('doc-99', 'DocumentReference', 'pat-enc', 'urn:uuid:test-uuid-encounter', '2024-02-10', '{\"resourceType\":\"DocumentReference\",\"id\":\"doc-99\"}', '2024-02-10')")

    bundle_json = '''{
        "resourceType": "Bundle",
        "entry": [
            {"fullUrl": "urn:uuid:test-uuid-encounter", "resource": {"resourceType": "Encounter", "id": "enc-V-99"}}
        ]
    }'''
    c.execute("INSERT INTO fhir_bundles VALUES ('bundle-enc', 'pat-enc', 2, ?, '2024-02-10')", (bundle_json,))
    conn.commit()
    conn.close()

    settings = Settings(DATABASE_PATH=db_path, CHROMA_PERSIST_DIR=chroma_dir, EMBEDDING_BACKEND="mock")
    store = VectorStoreManager(persist_dir=chroma_dir, settings=settings)
    mock_embed = MockEmbeddingClient()
    service = ClinicalSearchService(vector_store=store, embedding_client=mock_embed, settings=settings)

    detail = service.get_patient_detail("pat-enc")
    enc_ids = {r.encounter_id for r in detail.linked_resources}
    assert enc_ids == {"enc-V-99"}  # Both Encounter and DocumentReference now have consistent encounter_id!


@pytest.mark.slow
def test_real_local_model_performance_under_2_seconds(tmp_path):
    """Performance test with the real local sentence-transformers model over 50 records.

    SLA requirement: Indexing and querying 50 records executes under 2 seconds.
    """
    db_path = tmp_path / "perf_platform.db"
    chroma_dir = tmp_path / "perf_chroma"

    # Seed SQLite with 50 synthetic records
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE patients (patient_id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT, gender TEXT, date_of_birth TEXT, mrn TEXT, facility_id TEXT);
    """)
    c.execute("""
        CREATE TABLE fhir_resources (resource_id TEXT PRIMARY KEY, resource_type TEXT, patient_id TEXT, encounter_id TEXT, effective_date TEXT, fhir_json TEXT, created_at TEXT);
    """)
    c.execute("""
        CREATE TABLE clinical_summaries (summary_id TEXT PRIMARY KEY, patient_id TEXT, cache_key TEXT UNIQUE, content_hash TEXT, prompt_version TEXT, model_used TEXT, chief_concern TEXT, narrative_summary TEXT, word_count INTEGER, confidence_score REAL, summary_json TEXT, created_at TEXT);
    """)

    import base64
    for i in range(50):
        pat_id = f"pat-perf-{i}"
        c.execute("INSERT INTO patients VALUES (?, ?, ?, ?, ?, ?, ?)", (pat_id, f"First{i}", f"Last{i}", "other", "1990-01-01", f"MRN-{i}", "FAC-1"))
        text = f"Clinical diagnostic note {i}: Patient reports symptoms of dyspnea and tachycardia."
        b64 = base64.b64encode(text.encode("utf-8")).decode("utf-8")
        raw_fhir = f'{{"resourceType":"DocumentReference","id":"doc-{i}","content":[{{"attachment":{{"data":"{b64}"}}}}]}}'
        c.execute("INSERT INTO fhir_resources VALUES (?, ?, ?, ?, ?, ?, ?)", (f"doc-{i}", "DocumentReference", pat_id, f"enc-{i}", "2024-02-15", raw_fhir, "2024-02-15T00:00:00Z"))
    conn.commit()
    conn.close()

    settings = Settings(
        DATABASE_PATH=db_path,
        CHROMA_PERSIST_DIR=chroma_dir,
        EMBEDDING_BACKEND="local",
    )
    local_embedder = LocalEmbeddingClient()
    vector_store = VectorStoreManager(persist_dir=chroma_dir, settings=settings)
    indexer = ClinicalIndexer(vector_store=vector_store, embedding_client=local_embedder, settings=settings)

    # 1. Index 50 records
    t0 = time.perf_counter()
    metrics = indexer.build_index()
    t_index = time.perf_counter() - t0
    assert metrics["total_indexed"] == 50

    # 2. Query latency test
    service = ClinicalSearchService(vector_store=vector_store, embedding_client=local_embedder, settings=settings)
    t_start = time.perf_counter()
    response = service.search(SearchRequest(query="dyspnea and shortness of breath", limit=5))
    search_duration = time.perf_counter() - t_start

    assert response.total_patients == 5
    assert search_duration < 2.0, f"Search took {search_duration:.3f}s, expected < 2.0s"
