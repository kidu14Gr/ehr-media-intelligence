"""Clinical Summarizer orchestrator service (Task 3).

Coordinates:
- Context extraction from SQLite / FHIR storage
- Cache lookup via (content_hash + PROMPT_VERSION + model_name)
- LLM generation with rate-limit/timeout retries and JSON repair
- Word count enforcement (< 200 words) with one LLM re-shorten attempt
- Clinical safety verification: splits documented diagnoses from unverified/flagged ones
- Explainable confidence scoring and mandatory medical disclaimer
"""
from datetime import datetime, timezone
import json
import logging
import re
from typing import List, Optional, Tuple
import uuid

from app.ai.cache import ClinicalSummaryCache
from app.ai.extractor import PatientClinicalContext, PatientContextExtractor
from app.ai.llm_client import BaseLLMClient, get_llm_client
from app.ai.prompt_templates import (
    CLINICAL_SUMMARY_SYSTEM_PROMPT,
    PROMPT_VERSION,
    format_patient_prompt,
)
from app.core.config import get_settings
from app.core.exceptions import SummaryParsingError
from app.schemas.summary import (
    ClinicalAnomaly,
    LLMRawSummaryOutput,
    MedicalMediaItem,
    StructuredClinicalSummary,
)

logger = logging.getLogger("ehr.summarizer")


class ClinicalSummarizer:
    """Orchestrates end-to-end clinical summarization with caching and safety checks."""

    def __init__(
        self,
        llm_client: Optional[BaseLLMClient] = None,
        cache: Optional[ClinicalSummaryCache] = None,
        extractor: Optional[PatientContextExtractor] = None,
    ) -> None:
        self.settings = get_settings()
        self.client = llm_client or get_llm_client()
        self.cache = cache or ClinicalSummaryCache()
        self.extractor = extractor or PatientContextExtractor()

    def summarize_patient(
        self,
        patient_id: str,
        force_refresh: bool = False,
    ) -> StructuredClinicalSummary:
        """Generates or retrieves a structured clinical summary for a patient."""
        # 1. Extract patient clinical context
        context = self.extractor.extract_context(patient_id)
        if not context:
            raise ValueError(f"Patient ID not found in database: {patient_id}")

        model_name = getattr(self.client, "model", self.settings.get_active_model_name())

        # 2. Compute Cache Key = content_hash + PROMPT_VERSION + model_name
        cache_key = self.cache.build_cache_key(
            content_hash=context.content_hash,
            prompt_version=PROMPT_VERSION,
            model_name=model_name,
        )

        # 3. Check Cache
        if self.settings.summary_cache_enabled and not force_refresh:
            cached_summary = self.cache.get(cache_key)
            if cached_summary:
                return cached_summary

        # 4. Format Prompt and invoke LLM
        prompt = format_patient_prompt(context)
        raw_response_text = self.client.generate(prompt, CLINICAL_SUMMARY_SYSTEM_PROMPT)

        # 5. Parse JSON output with one repair attempt if needed
        raw_output = self._parse_raw_output(raw_response_text)

        # 6. Word count validation and shortening (< 200 words)
        final_narrative, word_count, warning = self._enforce_word_count(
            raw_output.narrative_summary
        )

        # 7. Clinical safety: verify diagnoses against source text
        documented, flagged = self._verify_diagnoses(
            candidate_diagnoses=raw_output.diagnoses,
            raw_source_text=context.raw_source_text,
            context=context,
        )

        # 8. Compute transparent confidence score
        confidence_score, reasons = self._calculate_confidence(
            context=context,
            documented_diagnoses=documented,
            flagged_diagnoses=flagged,
            anomalies=raw_output.notable_anomalies,
        )

        # 9. Clean up chief concern (null when missing)
        chief_concern = raw_output.chief_concern
        if chief_concern and chief_concern.strip().lower() in ("null", "none", "n/a", "not documented", "unspecified", "not specified"):
            chief_concern = None
        elif chief_concern:
            chief_concern = chief_concern.strip()

        # 10. Build final StructuredClinicalSummary
        summary = StructuredClinicalSummary(
            summary_id=f"sum-{uuid.uuid4().hex[:12]}",
            patient_id=context.patient_id,
            content_hash=context.content_hash,
            prompt_version=PROMPT_VERSION,
            model_used=model_name,
            generated_at=datetime.now(timezone.utc),
            chief_concern=chief_concern,
            documented_diagnoses=documented,
            unverified_or_flagged_diagnoses=flagged,
            key_findings=raw_output.key_findings,
            recent_medical_media=raw_output.recent_medical_media,
            notable_anomalies=raw_output.notable_anomalies,
            narrative_summary=final_narrative,
            word_count=word_count,
            word_count_warning=warning,
            confidence_score=confidence_score,
            confidence_reasons=reasons,
            is_cached=False,
        )

        # 10. Store in Cache (SQLite primary + JSON secondary)
        if self.settings.summary_cache_enabled:
            self.cache.store(summary, cache_key)

        return summary

    def _parse_raw_output(self, response_text: str) -> LLMRawSummaryOutput:
        """Parses LLM response JSON, stripping markdown code blocks and retrying repair once."""
        clean_text = self._extract_json_block(response_text)
        first_err_str = ""

        try:
            data = json.loads(clean_text)
            return LLMRawSummaryOutput.model_validate(data)
        except Exception as e:
            first_err_str = str(e)
            logger.warning("Primary JSON parse failed: %s. Attempting repair...", e)

        # Attempt one JSON repair call
        try:
            repaired_text = self.client.repair_json(
                malformed_json=clean_text,
                parse_error=first_err_str,
                system_prompt=CLINICAL_SUMMARY_SYSTEM_PROMPT,
            )
            clean_repaired = self._extract_json_block(repaired_text)
            data = json.loads(clean_repaired)
            return LLMRawSummaryOutput.model_validate(data)
        except Exception as repair_err:
            raise SummaryParsingError(
                f"Failed to parse LLM response into clinical summary schema: {repair_err}"
            ) from repair_err

    @staticmethod
    def _extract_json_block(text: str) -> str:
        """Extracts JSON substring between first { and last } if surrounded by markdown or commentary."""
        t = text.strip()
        if t.startswith("```"):
            t = re.sub(r"^```(?:json)?\s*", "", t)
            t = re.sub(r"\s*```$", "", t)
        first_brace = t.find("{")
        last_brace = t.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            return t[first_brace : last_brace + 1]
        return t

    def _enforce_word_count(self, narrative: str) -> Tuple[str, int, Optional[str]]:
        """Validates word count; re-calls LLM once to shorten if > 200 words, truncates as last resort."""
        words = narrative.strip().split()
        count = len(words)
        limit = self.settings.summary_word_limit

        if count <= limit:
            return narrative, count, None

        logger.info("Narrative summary exceeds limit (%d > %d words). Re-calling LLM to shorten...", count, limit)
        warning_msg = None

        try:
            shortened = self.client.shorten(
                original_summary=narrative,
                current_word_count=count,
                system_prompt=CLINICAL_SUMMARY_SYSTEM_PROMPT,
            )
            shortened_words = shortened.strip().split()
            shortened_count = len(shortened_words)

            if shortened_count <= limit:
                return shortened, shortened_count, f"Original narrative had {count} words; shortened to {shortened_count} words via LLM re-call."
        except Exception as e:
            logger.warning("LLM shorten call failed: %s", e)

        # Truncate at sentence boundary as last resort
        sentences = re.split(r"(?<=[.!?])\s+", narrative)
        truncated_sentences: List[str] = []
        running_count = 0

        for s in sentences:
            s_words = len(s.split())
            if running_count + s_words <= limit:
                truncated_sentences.append(s)
                running_count += s_words
            else:
                break

        if truncated_sentences:
            final_text = " ".join(truncated_sentences)
            final_count = len(final_text.split())
            warning_msg = (
                f"Narrative exceeded {limit} words ({count} words). "
                f"Condensation applied; sentence-truncated to {final_count} words as last resort."
            )
            return final_text, final_count, warning_msg

        # Ultimate fallback: hard word slice
        fallback_text = " ".join(words[:limit]) + "..."
        final_count = len(fallback_text.split())
        warning_msg = f"Narrative exceeded {limit} words ({count} words). Hard truncated to {final_count} words."
        return fallback_text, final_count, warning_msg

    def _verify_diagnoses(
        self,
        candidate_diagnoses: List[str],
        raw_source_text: str,
        context: PatientClinicalContext,
    ) -> Tuple[List[str], List[str]]:
        """Splits candidate diagnoses into documented vs unverified/flagged based on verbatim source text."""
        documented: List[str] = []
        flagged: List[str] = []

        # Collect documented diagnoses from source records
        explicit_source_diagnoses = set()
        for doc in context.documents:
            for d in doc.diagnoses:
                explicit_source_diagnoses.add(d.lower().strip())

        source_lower = raw_source_text.lower()

        for diag in candidate_diagnoses:
            diag_clean = diag.strip()
            diag_lower = diag_clean.lower()

            # 1. Exact or substring match in documented source diagnoses
            if any(diag_lower in exp or exp in diag_lower for exp in explicit_source_diagnoses):
                documented.append(diag_clean)
                continue

            # 2. Key clinical tokens present in raw source narrative
            tokens = [t for t in re.split(r"[^a-zA-Z0-9]+", diag_lower) if len(t) >= 4]
            if tokens and all(t in source_lower for t in tokens):
                documented.append(diag_clean)
            elif diag_lower in source_lower:
                documented.append(diag_clean)
            else:
                # Diagnosis NOT substantiated by source text
                flagged.append(diag_clean)
                logger.warning(
                    "Safety Alert: Candidate diagnosis '%s' not substantiated in source text. Isolated.",
                    diag_clean,
                )

        return documented, flagged

    def _calculate_confidence(
        self,
        context: PatientClinicalContext,
        documented_diagnoses: List[str],
        flagged_diagnoses: List[str],
        anomalies: List[ClinicalAnomaly],
    ) -> Tuple[float, List[str]]:
        """Calculates deterministic, explainable confidence score."""
        score = 0.50
        reasons: List[str] = []

        # 1. Document diversity
        has_notes = any(d.resource_type == "DocumentReference" for d in context.documents)
        has_reports = any(d.resource_type == "DiagnosticReport" for d in context.documents)
        if has_notes and has_reports:
            score += 0.20
            reasons.append("Multi-modal record continuity (both clinical notes and diagnostic reports present).")
        elif has_notes or has_reports:
            score += 0.10
            reasons.append("Single document modality available.")

        # 2. Date completeness
        undated = [d for d in context.documents if not d.date]
        if not undated and context.documents:
            score += 0.15
            reasons.append("Full temporal consistency: all documents possess valid clinical dates.")
        elif undated:
            score += 0.05
            reasons.append(f"Temporal ambiguity: {len(undated)} record(s) have unspecified dates.")

        # 3. Diagnostic verification
        if flagged_diagnoses:
            score -= 0.15
            reasons.append(
                f"Safety warning: {len(flagged_diagnoses)} diagnosis(es) could not be substantiated from source text."
            )
        elif documented_diagnoses:
            score += 0.15
            reasons.append(f"All {len(documented_diagnoses)} diagnoses substantiated directly from source text.")
        else:
            score += 0.10
            reasons.append("No active diagnoses detected; baseline health maintenance profile.")

        # Input-completeness confidence capped at 0.95
        score = max(0.1, min(0.95, round(score, 2)))
        return score, reasons
