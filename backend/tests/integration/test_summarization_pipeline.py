"""Integration tests for Task 3 Clinical Summarization Pipeline.

Validates:
- End-to-end summarization against SQLite database and FHIR resources
- Dual persistence in SQLite clinical_summaries table and JSON cache files
- 100% cache hit on second run with zero mock LLM calls
- One optional live smoke test (skipped by default unless RUN_LIVE_TESTS=1)
"""
import os
from pathlib import Path
import sqlite3
import pytest

from app.ai.cache import ClinicalSummaryCache
from app.ai.extractor import PatientContextExtractor
from app.ai.llm_client import GroqLLMClient, MockLLMClient
from app.ai.prompt_templates import PROMPT_VERSION
from app.ai.summarizer import ClinicalSummarizer
from app.core.config import get_settings


@pytest.fixture
def test_db_path() -> Path:
    backend_dir = Path(__file__).resolve().parents[2]
    return backend_dir.parent / "storage" / "clinical_platform.db"


def test_end_to_end_summarization_cohort(test_db_path: Path, tmp_path: Path):
    """Verify complete summarization pipeline across cohort using deterministic Mock client."""
    assert test_db_path.exists(), "Platform database required for integration test"

    # Set up isolated test cache
    cache_db = tmp_path / "integration_cache.db"
    cache_dir = tmp_path / "json_cache"
    cache = ClinicalSummaryCache(db_path=cache_db, cache_dir=cache_dir)

    mock_client = MockLLMClient()
    extractor = PatientContextExtractor(db_path=test_db_path)
    summarizer = ClinicalSummarizer(
        llm_client=mock_client,
        cache=cache,
        extractor=extractor,
    )

    # 1. Fetch cohort patient IDs
    with sqlite3.connect(test_db_path) as conn:
        cursor = conn.cursor()
        patients = cursor.execute("SELECT patient_id FROM patients ORDER BY patient_id;").fetchall()

    assert len(patients) == 5

    # 2. First Pass: Fresh generation for all 5 patients
    summaries_run1 = []
    for (pid,) in patients:
        s = summarizer.summarize_patient(pid)
        assert s.is_cached is False
        assert s.word_count <= 200
        assert s.confidence_score <= 0.95
        assert s.prompt_version == PROMPT_VERSION
        assert s.patient_id == pid
        summaries_run1.append(s)

    assert len(summaries_run1) == 5
    assert mock_client.call_count == 5

    # Verify SQLite cache records
    with sqlite3.connect(cache_db) as conn:
        cursor = conn.cursor()
        row_count = cursor.execute("SELECT count(*) FROM clinical_summaries;").fetchone()[0]
        assert row_count == 5

    # Verify JSON cache files
    json_files = list(cache_dir.glob("*.json"))
    assert len(json_files) == 5

    # 3. Second Pass: 100% Cache HIT — Zero LLM calls
    summaries_run2 = []
    for (pid,) in patients:
        s = summarizer.summarize_patient(pid)
        assert s.is_cached is True
        summaries_run2.append(s)

    assert len(summaries_run2) == 5
    # Call count must remain exactly 5 (zero new calls!)
    assert mock_client.call_count == 5


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TESTS") != "1" or not os.getenv("GROQ_API_KEY"),
    reason="Live API tests skipped by default. Run with RUN_LIVE_TESTS=1 GROQ_API_KEY=... to enable.",
)
def test_live_groq_smoke_test(test_db_path: Path, tmp_path: Path):
    """Optional smoke test connecting directly to live Groq API (skipped by default)."""
    settings = get_settings()
    if not settings.groq_api_key:
        pytest.skip("GROQ_API_KEY not configured")

    client = GroqLLMClient()
    extractor = PatientContextExtractor(db_path=test_db_path)
    cache = ClinicalSummaryCache(db_path=tmp_path / "live.db", cache_dir=tmp_path / "cache")
    summarizer = ClinicalSummarizer(llm_client=client, cache=cache, extractor=extractor)

    # Test on David Kim
    summary = summarizer.summarize_patient("pat-REC-3703B61FE28A", force_refresh=True)
    assert summary.patient_id == "pat-REC-3703B61FE28A"
    assert summary.word_count <= 200
    assert summary.confidence_score <= 0.95
    assert isinstance(summary.documented_diagnoses, list)
