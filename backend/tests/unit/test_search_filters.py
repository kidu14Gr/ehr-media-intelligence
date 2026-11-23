"""Unit tests for date parsing, PHI sanitization, Chroma filter building, and backend checks."""
import pytest

from app.core.exceptions import IndexBackendMismatchError
from app.search.indexer import parse_date_to_int, sanitize_clinical_text
from app.search.service import ClinicalSearchService
from app.search.vector_store import VectorStoreManager


def test_parse_date_to_int():
    """Validates date extraction into (has_date, date_str, date_int)."""
    # Standard ISO date
    has_date, d_str, d_int = parse_date_to_int("2024-02-16")
    assert has_date is True
    assert d_str == "2024-02-16"
    assert d_int == 20240216

    # Full timestamp
    has_date, d_str, d_int = parse_date_to_int("2024-02-16 08:00:00+00:00")
    assert has_date is True
    assert d_str == "2024-02-16"
    assert d_int == 20240216

    # Missing / None
    has_date, d_str, d_int = parse_date_to_int(None)
    assert has_date is False
    assert d_str == ""
    assert d_int == 0

    # Empty string
    has_date, d_str, d_int = parse_date_to_int("   ")
    assert has_date is False
    assert d_str == ""
    assert d_int == 0


def test_sanitize_clinical_text():
    """Validates removal of direct PHI header lines from indexed narrative text."""
    raw = (
        "Patient: John Doe\n"
        "MRN: MRN-12345\n"
        "DOB: 1980-05-14\n"
        "Facility: Central Hospital\n"
        "Clinical Progress Note:\n"
        "Patient presented with severe wheezing and productive cough."
    )
    cleaned = sanitize_clinical_text(raw)
    assert "John Doe" not in cleaned
    assert "MRN-12345" not in cleaned
    assert "Central Hospital" not in cleaned
    assert "Clinical Progress Note:" in cleaned
    assert "severe wheezing" in cleaned


def test_chroma_filter_builder_no_date():
    """Validates that without date filters, undated records are not excluded."""
    service = ClinicalSearchService(embedding_client=None, vector_store=None)

    # Completely unfiltered
    f_all = service._build_chroma_filter(resource_type=None, start_date=None, end_date=None)
    assert f_all is None

    # Filtered only by resource_type
    f_res = service._build_chroma_filter(
        resource_type="DocumentReference",
        start_date=None,
        end_date=None,
    )
    assert f_res == {"resource_type": {"$eq": "DocumentReference"}}


def test_chroma_filter_builder_with_date_range():
    """Validates that date filters enforce has_date=True and integer bounds."""
    service = ClinicalSearchService(embedding_client=None, vector_store=None)

    # Date range filter
    f_date = service._build_chroma_filter(
        resource_type=None,
        start_date="2024-01-01",
        end_date="2024-03-31",
    )
    assert "$and" in f_date
    conditions = f_date["$and"]
    assert {"has_date": {"$eq": True}} in conditions
    assert {"record_date_int": {"$gte": 20240101}} in conditions
    assert {"record_date_int": {"$lte": 20240331}} in conditions

    # Combined resource_type and date range
    f_comb = service._build_chroma_filter(
        resource_type="DiagnosticReport",
        start_date="2024-02-01",
        end_date=None,
    )
    assert "$and" in f_comb
    assert {"resource_type": {"$eq": "DiagnosticReport"}} in f_comb["$and"]
    assert {"has_date": {"$eq": True}} in f_comb["$and"]
    assert {"record_date_int": {"$gte": 20240201}} in f_comb["$and"]


def test_backend_mismatch_detection(tmp_path):
    """Validates that VectorStoreManager raises IndexBackendMismatchError when configuration differs."""
    store = VectorStoreManager(persist_dir=tmp_path)
    store.save_manifest(
        backend_name="huggingface",
        model_name="sentence-transformers/all-MiniLM-L6-v2",
        dimension=384,
        document_count=10,
    )

    # Should succeed when backend and model match
    store.verify_backend_compatibility(
        expected_backend="huggingface",
        expected_model="sentence-transformers/all-MiniLM-L6-v2",
    )

    # Should raise error when active backend is local
    with pytest.raises(IndexBackendMismatchError, match="Vector index was built with backend 'huggingface'"):
        store.verify_backend_compatibility(
            expected_backend="local",
            expected_model="sentence-transformers/all-MiniLM-L6-v2",
        )


def test_content_hash_skips_unchanged_and_updates_edited(tmp_path):
    """Validates that unchanged records are skipped and modifying one updates exactly one vector."""
    import base64
    import sqlite3
    from app.core.config import Settings
    from app.search.embeddings import MockEmbeddingClient
    from app.search.indexer import ClinicalIndexer

    db_path = tmp_path / "hash_test.db"
    chroma_dir = tmp_path / "hash_chroma"

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("CREATE TABLE patients (patient_id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT, gender TEXT, date_of_birth TEXT, mrn TEXT, facility_id TEXT)")
    c.execute("CREATE TABLE fhir_resources (resource_id TEXT PRIMARY KEY, resource_type TEXT, patient_id TEXT, encounter_id TEXT, effective_date TEXT, fhir_json TEXT, created_at TEXT)")
    c.execute("CREATE TABLE clinical_summaries (summary_id TEXT PRIMARY KEY, patient_id TEXT, cache_key TEXT UNIQUE, content_hash TEXT, prompt_version TEXT, model_used TEXT, chief_concern TEXT, narrative_summary TEXT, word_count INTEGER, confidence_score REAL, summary_json TEXT, created_at TEXT)")

    # Insert 2 documents and 1 summary
    b64_1 = base64.b64encode(b"Patient has mild cough.").decode()
    b64_2 = base64.b64encode(b"Lab report shows normal glucose.").decode()

    c.execute("INSERT INTO fhir_resources VALUES (?, ?, ?, ?, ?, ?, ?)",
              ("doc-1", "DocumentReference", "pat-1", "enc-1", "2024-02-10", f'{{"resourceType":"DocumentReference","content":[{{"attachment":{{"data":"{b64_1}"}}}}]}}', "2024-02-10"))
    c.execute("INSERT INTO fhir_resources VALUES (?, ?, ?, ?, ?, ?, ?)",
              ("doc-2", "DocumentReference", "pat-2", "enc-2", "2024-02-11", f'{{"resourceType":"DocumentReference","content":[{{"attachment":{{"data":"{b64_2}"}}}}]}}', "2024-02-11"))
    c.execute("INSERT INTO clinical_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
              ("sum-1", "pat-1", "k1", "h1", "v1.2", "mock", "Cough", "Mild cough resolving.", 3, 0.9, '{"key_findings":[]}', "2024-02-10"))
    conn.commit()
    conn.close()

    settings = Settings(DATABASE_PATH=db_path, CHROMA_PERSIST_DIR=chroma_dir, EMBEDDING_BACKEND="mock")
    store = VectorStoreManager(persist_dir=chroma_dir, settings=settings)
    mock_embed = MockEmbeddingClient()
    indexer = ClinicalIndexer(vector_store=store, embedding_client=mock_embed, settings=settings)

    # 1. Initial build: 3 upserted, 0 skipped
    m1 = indexer.build_index()
    assert m1["total_indexed"] == 3
    assert m1["upserted"] == 3
    assert m1["skipped"] == 0

    # 2. Re-index with zero edits: 0 upserted, 3 skipped
    m2 = indexer.build_index()
    assert m2["total_indexed"] == 3
    assert m2["upserted"] == 0
    assert m2["skipped"] == 3

    # 3. Edit exactly ONE document in SQLite
    b64_1_edited = base64.b64encode(b"Patient now reports severe acute dyspnea and wheezing.").decode()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("UPDATE fhir_resources SET fhir_json = ? WHERE resource_id = 'doc-1'",
              (f'{{"resourceType":"DocumentReference","content":[{{"attachment":{{"data":"{b64_1_edited}"}}}}]}}',))
    conn.commit()
    conn.close()

    # 4. Re-index: exactly 1 upserted, 2 skipped
    m3 = indexer.build_index()
    assert m3["total_indexed"] == 3
    assert m3["upserted"] == 1
    assert m3["skipped"] == 2


def test_extract_sentence_aware_snippet():
    """Validates that snippets break on sentence boundaries and meet ~150 char target."""
    from app.search.indexer import extract_sentence_aware_snippet

    # Short text returned as-is
    short = "Short clinical observation."
    assert extract_sentence_aware_snippet(short, min_chars=150) == short

    # Multi-sentence text
    text = (
        "First sentence discusses hypertension. "
        "Second sentence notes adherence to medication and diet. "
        "Third sentence mentions scheduled lab testing for next week. "
        "Fourth sentence is extra detail that should be omitted."
    )
    snippet = extract_sentence_aware_snippet(text, min_chars=150)
    assert len(snippet) >= 150
    assert snippet.endswith(".")
    assert "Fourth sentence" not in snippet
    assert "Third sentence mentions scheduled lab testing for next week." in snippet


def test_summary_vector_uses_latest_source_document_date_not_generation_date(tmp_path):
    """Validates summary vectors receive latest source document date, never generation date."""
    import base64
    import sqlite3
    from app.core.config import Settings
    from app.search.embeddings import MockEmbeddingClient
    from app.search.indexer import ClinicalIndexer

    db_path = tmp_path / "date_test.db"
    chroma_dir = tmp_path / "date_chroma"

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("CREATE TABLE patients (patient_id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT, gender TEXT, date_of_birth TEXT, mrn TEXT, facility_id TEXT)")
    c.execute("CREATE TABLE fhir_resources (resource_id TEXT PRIMARY KEY, resource_type TEXT, patient_id TEXT, encounter_id TEXT, effective_date TEXT, fhir_json TEXT, created_at TEXT)")
    c.execute("CREATE TABLE clinical_summaries (summary_id TEXT PRIMARY KEY, patient_id TEXT, cache_key TEXT UNIQUE, content_hash TEXT, prompt_version TEXT, model_used TEXT, chief_concern TEXT, narrative_summary TEXT, word_count INTEGER, confidence_score REAL, summary_json TEXT, created_at TEXT)")

    # Patient with dated document on 2024-02-14, summary created at 2026-11-23
    b64 = base64.b64encode(b"Clinical note.").decode()
    c.execute("INSERT INTO fhir_resources VALUES (?, ?, ?, ?, ?, ?, ?)",
              ("doc-1", "DocumentReference", "pat-dated", "enc-1", "2024-02-14 10:00:00+00:00", f'{{"resourceType":"DocumentReference","content":[{{"attachment":{{"data":"{b64}"}}}}]}}', "2024-02-14"))
    c.execute("INSERT INTO clinical_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
              ("sum-1", "pat-dated", "k1", "h1", "v1.2", "mock", "Follow-up", "Patient is doing well.", 5, 0.9, '{"key_findings":[]}', "2026-11-23T12:00:00Z"))

    # Patient with only undated document (effective_date is NULL)
    c.execute("INSERT INTO fhir_resources VALUES (?, ?, ?, ?, ?, ?, ?)",
              ("doc-2", "DocumentReference", "pat-undated", "enc-2", None, f'{{"resourceType":"DocumentReference","content":[{{"attachment":{{"data":"{b64}"}}}}]}}', "2024-02-14"))
    c.execute("INSERT INTO clinical_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
              ("sum-2", "pat-undated", "k2", "h2", "v1.2", "mock", "Follow-up", "Undated patient summary.", 5, 0.8, '{"key_findings":[]}', "2026-11-23T12:00:00Z"))
    conn.commit()
    conn.close()

    settings = Settings(DATABASE_PATH=db_path, CHROMA_PERSIST_DIR=chroma_dir, EMBEDDING_BACKEND="mock")
    store = VectorStoreManager(persist_dir=chroma_dir, settings=settings)
    mock_embed = MockEmbeddingClient()
    indexer = ClinicalIndexer(vector_store=store, embedding_client=mock_embed, settings=settings)
    indexer.build_index()

    docs = store.collection.get(include=["metadatas"])
    metas = {id_: meta for id_, meta in zip(docs["ids"], docs["metadatas"])}

    # Dated patient summary vector must use source document date (2024-02-14), NEVER 2026-11-23
    assert metas["sum-pat-dated"]["record_date"] == "2024-02-14"
    assert metas["sum-pat-dated"]["has_date"] is True
    assert metas["sum-pat-dated"]["record_date_int"] == 20240214

    # Undated patient summary vector must have has_date=False and record_date=""
    assert metas["sum-pat-undated"]["has_date"] is False
    assert metas["sum-pat-undated"]["record_date"] == ""
    assert metas["sum-pat-undated"]["record_date_int"] == 0
