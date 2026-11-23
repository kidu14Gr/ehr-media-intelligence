"""Unit tests for Clinical Summarizer, Content Hashing, Caching, and Safety Guardrails."""
from datetime import datetime, timezone
import logging
from pathlib import Path
import pytest

from app.ai.cache import ClinicalSummaryCache
from app.ai.extractor import ExtractedDocument, PatientClinicalContext, PatientContextExtractor
from app.ai.llm_client import MockLLMClient
from app.ai.prompt_templates import PROMPT_VERSION
from app.ai.summarizer import ClinicalSummarizer
from app.schemas.summary import (
    ClinicalAnomaly,
    MedicalMediaItem,
    StructuredClinicalSummary,
)


@pytest.fixture
def sample_context() -> PatientClinicalContext:
    doc1 = ExtractedDocument(
        document_id="DOC-01",
        resource_type="DocumentReference",
        title="Outpatient Note",
        date="2024-01-15",
        category="Clinical Note",
        text_content="Patient reports chronic headache. Blood pressure 145/90 mmHg. Diagnosed with essential hypertension.",
        diagnoses=["Essential Hypertension"],
    )
    doc2 = ExtractedDocument(
        document_id="DOC-02",
        resource_type="DiagnosticReport",
        title="Chest Radiograph",
        date="2024-01-16",
        category="Radiology",
        text_content="Lungs clear bilaterally. No focal consolidation.",
        diagnoses=[],
    )
    docs = [doc1, doc2]
    raw_text = "\n\n".join(d.text_content for d in docs)
    content_hash = PatientContextExtractor.compute_content_hash(
        patient_id="pat-TEST-001",
        dob="1980-05-12",
        gender="female",
        documents=docs,
    )
    return PatientClinicalContext(
        patient_id="pat-TEST-001",
        mrn="MRN-TEST-001",
        facility_id="CLINIC-A",
        first_name="Jane",
        last_name="Doe",
        date_of_birth="1980-05-12",
        gender="female",
        documents=docs,
        raw_source_text=raw_text,
        content_hash=content_hash,
    )


@pytest.fixture
def temp_cache(tmp_path: Path) -> ClinicalSummaryCache:
    db_path = tmp_path / "test_summaries.db"
    cache_dir = tmp_path / "cache"
    return ClinicalSummaryCache(db_path=db_path, cache_dir=cache_dir)


def test_mock_llm_invented_diagnosis_flagged(sample_context: PatientClinicalContext, temp_cache: ClinicalSummaryCache):
    """Verify that an invented/hallucinated diagnosis from LLM is moved to unverified_or_flagged_diagnoses."""
    # Configure Mock to inject hallucinated diagnosis
    mock_client = MockLLMClient(simulate_hallucinated_diagnosis=True)

    class StubExtractor:
        def extract_context(self, pid: str):
            return sample_context

    summarizer = ClinicalSummarizer(
        llm_client=mock_client,
        cache=temp_cache,
        extractor=StubExtractor(),  # type: ignore
    )

    summary = summarizer.summarize_patient("pat-TEST-001", force_refresh=True)

    # The invented diagnosis must NOT be in documented_diagnoses
    assert "Acute Appendicitis Hallucinated" not in summary.documented_diagnoses
    # The invented diagnosis MUST be recorded in unverified_or_flagged_diagnoses
    assert "Acute Appendicitis Hallucinated" in summary.unverified_or_flagged_diagnoses
    # Real diagnosis substantiated in source text must be in documented_diagnoses
    assert "Essential Hypertension" in summary.documented_diagnoses


def test_changing_patient_data_invalidates_cache(sample_context: PatientClinicalContext, temp_cache: ClinicalSummaryCache):
    """Verify that modifying patient data produces a new content hash and invalidates the cache."""
    mock_client = MockLLMClient()

    class DynamicExtractor:
        def __init__(self, ctx: PatientClinicalContext):
            self.ctx = ctx
        def extract_context(self, pid: str):
            return self.ctx

    extractor = DynamicExtractor(sample_context)
    summarizer = ClinicalSummarizer(llm_client=mock_client, cache=temp_cache, extractor=extractor)  # type: ignore

    # 1. First summarization: generates fresh summary
    summary1 = summarizer.summarize_patient("pat-TEST-001")
    assert summary1.is_cached is False
    assert mock_client.call_count == 1

    # 2. Second summarization with identical data: cache HIT
    summary2 = summarizer.summarize_patient("pat-TEST-001")
    assert summary2.is_cached is True
    assert mock_client.call_count == 1  # Zero new API calls

    # 3. Modify patient data: add new diagnostic document
    new_doc = ExtractedDocument(
        document_id="DOC-03",
        resource_type="DiagnosticReport",
        title="Metabolic Panel",
        date="2024-02-01",
        category="Laboratory",
        text_content="Fasting blood glucose 135 mg/dL. Elevated.",
        diagnoses=["Hyperglycemia"],
    )
    updated_docs = sample_context.documents + [new_doc]
    new_raw_text = sample_context.raw_source_text + "\n\n" + new_doc.text_content
    new_hash = PatientContextExtractor.compute_content_hash(
        patient_id=sample_context.patient_id,
        dob=sample_context.date_of_birth,
        gender=sample_context.gender,
        documents=updated_docs,
    )
    assert new_hash != sample_context.content_hash

    # Update context
    extractor.ctx = PatientClinicalContext(
        patient_id=sample_context.patient_id,
        mrn=sample_context.mrn,
        facility_id=sample_context.facility_id,
        first_name=sample_context.first_name,
        last_name=sample_context.last_name,
        date_of_birth=sample_context.date_of_birth,
        gender=sample_context.gender,
        documents=updated_docs,
        raw_source_text=new_raw_text,
        content_hash=new_hash,
    )

    # 4. Third summarization with modified data: cache MISS, triggers new API call
    summary3 = summarizer.summarize_patient("pat-TEST-001")
    assert summary3.is_cached is False
    assert mock_client.call_count == 2
    assert summary3.content_hash == new_hash


def test_changing_prompt_version_invalidates_cache(sample_context: PatientClinicalContext, temp_cache: ClinicalSummaryCache):
    """Verify that bumping PROMPT_VERSION invalidates the cache key even on identical data."""
    mock_client = MockLLMClient()

    class StubExtractor:
        def extract_context(self, pid: str):
            return sample_context

    summarizer = ClinicalSummarizer(llm_client=mock_client, cache=temp_cache, extractor=StubExtractor())  # type: ignore

    # 1. Summarize under current prompt version
    summary = summarizer.summarize_patient("pat-TEST-001")
    assert summary.prompt_version == PROMPT_VERSION

    # 2. Key with current version hits cache
    key_current = temp_cache.build_cache_key(
        content_hash=sample_context.content_hash,
        prompt_version=PROMPT_VERSION,
        model_name=summary.model_used,
    )
    assert temp_cache.get(key_current) is not None

    # 3. Key with different/bumped prompt version produces a cache miss
    key_bumped = temp_cache.build_cache_key(
        content_hash=sample_context.content_hash,
        prompt_version="v99.9",
        model_name=summary.model_used,
    )
    assert key_current != key_bumped
    assert temp_cache.get(key_bumped) is None


def test_confidence_score_capped_at_095(sample_context: PatientClinicalContext, temp_cache: ClinicalSummaryCache):
    """Verify that confidence (input-completeness) is capped at 0.95."""
    mock_client = MockLLMClient()

    class StubExtractor:
        def extract_context(self, pid: str):
            return sample_context

    summarizer = ClinicalSummarizer(llm_client=mock_client, cache=temp_cache, extractor=StubExtractor())  # type: ignore
    summary = summarizer.summarize_patient("pat-TEST-001", force_refresh=True)

    assert summary.confidence_score <= 0.95
    assert summary.confidence_score >= 0.10


def test_chief_concern_null_when_missing(sample_context: PatientClinicalContext, temp_cache: ClinicalSummaryCache):
    """Verify that chief concern is converted to None when missing or unspecified."""
    mock_client = MockLLMClient()
    # Override client output to have "Not documented"
    orig_generate = mock_client.generate
    def custom_generate(prompt, sys_prompt):
        import json
        raw = json.loads(orig_generate(prompt, sys_prompt))
        raw["chief_concern"] = "N/A"
        return json.dumps(raw)

    mock_client.generate = custom_generate  # type: ignore

    class StubExtractor:
        def extract_context(self, pid: str):
            return sample_context

    summarizer = ClinicalSummarizer(llm_client=mock_client, cache=temp_cache, extractor=StubExtractor())  # type: ignore
    summary = summarizer.summarize_patient("pat-TEST-001", force_refresh=True)

    assert summary.chief_concern is None


def test_word_count_enforcement_shortening(sample_context: PatientClinicalContext, temp_cache: ClinicalSummaryCache):
    """Verify that narrative over 200 words triggers shorten re-call."""
    mock_client = MockLLMClient(simulate_overlength=True)

    class StubExtractor:
        def extract_context(self, pid: str):
            return sample_context

    summarizer = ClinicalSummarizer(llm_client=mock_client, cache=temp_cache, extractor=StubExtractor())  # type: ignore
    summary = summarizer.summarize_patient("pat-TEST-001", force_refresh=True)

    # Verify shorten was called
    assert mock_client.shorten_call_count == 1
    # Verify final narrative complies with 200 words limit
    assert summary.word_count <= 200
    assert summary.word_count_warning is not None


def test_no_phi_in_llm_logging(caplog: pytest.LogCaptureFixture):
    """Verify that LLM client logging contains zero patient PHI (no names, MRNs, notes)."""
    with caplog.at_level(logging.INFO, logger="ehr.llm"):
        from app.ai.llm_client import GroqLLMClient
        client = GroqLLMClient(api_key="mock_key", model="test-model")

        # Fake prompt containing sensitive PHI
        sensitive_prompt = "Patient Name: John Doe. MRN: MRN-9999. Clinical Note: Severe chest pain radiating to left arm."
        sensitive_sys = "You are a clinical AI."

        try:
            client._execute_with_retry(sensitive_prompt, sensitive_sys)
        except Exception:
            pass

        log_text = " ".join(record.message for record in caplog.records)
        assert "John Doe" not in log_text
        assert "MRN-9999" not in log_text
        assert "Severe chest pain" not in log_text
