"""LLM Client Abstraction, Groq Integration, and Mock Client for Task 3.

Enforces:
- Abstract interface: BaseLLMClient
- Concrete Groq client using official groq SDK with native JSON mode
- Deterministic MockLLMClient for offline testing and CI
- Configuration exclusively from Settings (.env) with no hardcoded models
- Strict PHI logging redaction (only logs lengths, latency, model names, never patient clinical data)
- Retry logic ONLY on rate-limits (HTTP 429) and timeouts (max 3 retries)
- One JSON-repair attempt for malformed outputs
- One LLM re-call to shorten if narrative exceeds 200 words
"""
from abc import ABC, abstractmethod
import json
import logging
import time
from typing import Any, Dict, List, Optional

from groq import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    Groq,
    RateLimitError,
)

from app.core.config import get_settings
from app.core.exceptions import (
    LLMAPIError,
    LLMConfigurationError,
    LLMRateLimitError,
    LLMTimeoutError,
    SummaryParsingError,
)

logger = logging.getLogger("ehr.llm")


class BaseLLMClient(ABC):
    """Abstract interface for LLM providers."""

    @abstractmethod
    def generate(self, prompt: str, system_prompt: str) -> str:
        """Generates a response from the LLM."""
        pass

    @abstractmethod
    def shorten(self, original_summary: str, current_word_count: int, system_prompt: str) -> str:
        """Re-calls LLM once to condense an over-length summary to ~150 words."""
        pass

    @abstractmethod
    def repair_json(self, malformed_json: str, parse_error: str, system_prompt: str) -> str:
        """Attempts one repair of malformed JSON output."""
        pass


class GroqLLMClient(BaseLLMClient):
    """Production Groq client with strict retry and privacy safeguards."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        temperature: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.groq_api_key
        self.model = model or settings.groq_model
        self.max_tokens = max_tokens or settings.llm_max_tokens
        self.timeout = timeout or settings.llm_timeout_seconds
        self.temperature = temperature if temperature is not None else settings.llm_temperature
        self.max_retries = max_retries or settings.llm_max_retries

        self._client: Optional[Groq] = None

    def _get_client(self) -> Groq:
        if not self.api_key:
            raise LLMConfigurationError(
                "GROQ_API_KEY is not configured. Please supply an API key in backend/.env"
            )
        if self._client is None:
            self._client = Groq(
                api_key=self.api_key,
                timeout=self.timeout,
            )
        return self._client

    def _execute_with_retry(self, prompt: str, system_prompt: str, json_mode: bool = True) -> str:
        """Executes Groq API call with retries ONLY on rate-limits and timeouts."""
        client = self._get_client()
        attempts = 0

        # Safe non-PHI logging
        logger.info(
            "Groq API call initiated [model=%s, prompt_len=%d chars, max_tokens=%d]",
            self.model,
            len(prompt),
            self.max_tokens,
        )

        response_format = {"type": "json_object"} if json_mode else None

        while attempts < self.max_retries:
            attempts += 1
            start_time = time.perf_counter()
            try:
                kwargs: Dict[str, Any] = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": self.temperature,
                    "max_tokens": max(self.max_tokens, 2048),
                }
                if response_format:
                    kwargs["response_format"] = response_format

                chat_completion = client.chat.completions.create(**kwargs)
                elapsed = time.perf_counter() - start_time

                text_output = chat_completion.choices[0].message.content or ""

                # Safe non-PHI logging: logs length and latency, NEVER narrative content
                logger.info(
                    "Groq API call succeeded [model=%s, response_len=%d chars, latency=%.2fs, attempt=%d]",
                    self.model,
                    len(text_output),
                    elapsed,
                    attempts,
                )
                return text_output

            except RateLimitError as e:
                elapsed = time.perf_counter() - start_time
                logger.warning(
                    "Groq RateLimitError on attempt %d/%d [latency=%.2fs]. Retrying...",
                    attempts,
                    self.max_retries,
                    elapsed,
                )
                if attempts >= self.max_retries:
                    raise LLMRateLimitError(f"Groq rate limit exceeded after {attempts} attempts: {e}") from e
                time.sleep(min(2**attempts, 8))

            except (APITimeoutError, TimeoutError) as e:
                elapsed = time.perf_counter() - start_time
                logger.warning(
                    "Groq APITimeoutError on attempt %d/%d [latency=%.2fs]. Retrying...",
                    attempts,
                    self.max_retries,
                    elapsed,
                )
                if attempts >= self.max_retries:
                    raise LLMTimeoutError(f"Groq request timed out after {attempts} attempts: {e}") from e
                time.sleep(min(2**attempts, 8))

            except APIConnectionError as e:
                # Transient connection error qualifies for timeout/retry
                if attempts >= self.max_retries:
                    raise LLMAPIError(f"Groq connection failed after {attempts} attempts: {e}") from e
                time.sleep(min(2**attempts, 8))

            except APIStatusError as e:
                # If Groq server-side grammar validation failed, retry immediately without response_format
                err_text = str(e).lower()
                if response_format and ("json_validate_failed" in err_text or "failed to generate json" in err_text):
                    logger.warning("Groq json_validate_failed. Retrying immediately without response_format...")
                    response_format = None
                    continue

                logger.error("Groq APIStatusError [status_code=%d]", e.status_code)
                raise LLMAPIError(f"Groq API error (status {e.status_code}): {e.message}") from e

            except Exception as e:
                raise LLMAPIError(f"Unexpected error communicating with Groq API: {e}") from e

        raise LLMAPIError(f"Failed to obtain response after {self.max_retries} attempts.")

    def generate(self, prompt: str, system_prompt: str) -> str:
        return self._execute_with_retry(prompt, system_prompt, json_mode=True)

    def shorten(self, original_summary: str, current_word_count: int, system_prompt: str) -> str:
        shorten_prompt = (
            f"The following clinical narrative summary is {current_word_count} words long, exceeding "
            f"the strict 200-word limit. Shorten it to approximately 150 words while retaining all critical "
            f"diagnoses, findings, and any 'Interpretation:' section:\n\n\"{original_summary}\"\n\n"
            f"Return ONLY the condensed narrative text (strictly under 200 words)."
        )
        return self._execute_with_retry(shorten_prompt, system_prompt, json_mode=False)

    def repair_json(self, malformed_json: str, parse_error: str, system_prompt: str) -> str:
        repair_prompt = (
            f"The previous output failed JSON parsing with error: {parse_error}\n\n"
            f"Malformed JSON:\n\"\"\"{malformed_json}\"\"\"\n\n"
            f"Please repair and output strictly valid JSON conforming to the schema. "
            f"Output JSON only, with no markdown code blocks or conversational text."
        )
        return self._execute_with_retry(repair_prompt, system_prompt, json_mode=True)


class MockLLMClient(BaseLLMClient):
    """Deterministic Mock LLM client for offline unit/integration testing with zero network calls."""

    def __init__(
        self,
        model: Optional[str] = None,
        simulate_rate_limit_attempts: int = 0,
        simulate_timeout: bool = False,
        simulate_malformed_json: bool = False,
        simulate_overlength: bool = False,
        simulate_hallucinated_diagnosis: bool = False,
    ) -> None:
        self.model = model or get_settings().groq_model
        self.simulate_rate_limit_attempts = simulate_rate_limit_attempts
        self.simulate_timeout = simulate_timeout
        self.simulate_malformed_json = simulate_malformed_json
        self.simulate_overlength = simulate_overlength
        self.simulate_hallucinated_diagnosis = simulate_hallucinated_diagnosis
        self.call_count = 0
        self.shorten_call_count = 0
        self.repair_call_count = 0

    def generate(self, prompt: str, system_prompt: str) -> str:
        self.call_count += 1

        if self.simulate_rate_limit_attempts > 0:
            self.simulate_rate_limit_attempts -= 1
            raise LLMRateLimitError("Simulated HTTP 429 Rate Limit Exceeded")

        if self.simulate_timeout:
            raise LLMTimeoutError("Simulated LLM API request timeout")

        if self.simulate_malformed_json:
            return '{"chief_concern": "Chest pain", "diagnoses": ["Hypertension"'  # Malformed JSON

        # Check patient in prompt
        # David Kim check: prompt contains DK-5021 or pat-REC-3703B61FE28A
        is_david_kim = "pat-REC-3703B61FE28A" in prompt or "Glucose 95 mg/dL" in prompt

        if is_david_kim and not self.simulate_hallucinated_diagnosis:
            # Expected for David Kim: no diagnoses, no anomalies, normal labs
            diagnoses = []
            key_findings = [
                "Glucose 95 mg/dL",
                "Creatinine 0.9 mg/dL",
                "ALT 24 U/L",
                "Patient reports feeling well",
            ]
            media = [
                {
                    "modality": "Lab Report",
                    "description": "Comprehensive Metabolic Panel",
                    "date": "2024-02-16",
                    "findings": "All metabolic panel values within normal reference range.",
                }
            ]
            anomalies = []
            narrative = (
                "The patient attended a scheduled outpatient follow-up consultation to review recent diagnostic findings. "
                "Laboratory assessment with a comprehensive metabolic panel demonstrated normal glucose, creatinine, and ALT levels. "
                "The patient reported feeling well and was advised on routine cardiovascular exercise. "
                "Interpretation: Stable metabolic profile with no evidence of acute systemic dysfunction or chronic organ impairment."
            )
        else:
            diagnoses = []
            if "Hypertension" in prompt or "Blood Pressure" in prompt or "BP" in prompt:
                diagnoses.append("Essential Hypertension")
            if "Type 2 Diabetes" in prompt or "Diabetes" in prompt:
                diagnoses.append("Type 2 Diabetes Mellitus")
            if "Pneumonia" in prompt or "Infiltrate" in prompt:
                diagnoses.append("Community-Acquired Pneumonia")
            if "Discharge" in prompt or "Discharge Summary" in prompt:
                diagnoses.append("Acute Myocardial Infarction")

            if self.simulate_hallucinated_diagnosis:
                diagnoses.append("Acute Appendicitis Hallucinated")

            key_findings = [
                "Physical examination and vital signs within normal parameters.",
            ]
            media = []
            if "Chest Radiograph" in prompt or "Imaging" in prompt:
                media.append(
                    {
                        "modality": "Chest X-Ray",
                        "description": "Diagnostic Medical Imaging Report",
                        "date": "2024-02-15",
                        "findings": "Lungs clear bilaterally with no focal consolidation.",
                    }
                )
            anomalies = []
            narrative = (
                "The patient presented for clinical evaluation and follow-up. Vital signs and physical examinations "
                "demonstrated stable parameters with no acute distress. Recent diagnostic reports and clinical documents "
                "were reviewed in detail. "
                "Interpretation: Documented clinical findings indicate controlled management under current regimens."
            )

        if self.simulate_overlength:
            # Strictly over 200 words to trigger shorten
            narrative = (
                "The patient presented for a comprehensive clinical consultation and review of clinical documentation. "
                "A thorough review of systems demonstrated negative results for acute cardiopulmonary complaints, dizziness, "
                "shortness of breath, chest pain, and peripheral edema. Vital signs demonstrated stable baseline measurements "
                "across blood pressure, heart rate, respiratory rate, and oxygen saturation. Cardiovascular evaluation confirmed "
                "regular rate and rhythm without murmurs, gallops, or friction rubs. Pulmonary examination showed lungs clear to "
                "auscultation bilaterally with vesicular breath sounds and absence of wheezing or rhonchi. Abdominal evaluation "
                "revealed soft, non-tender quadrants with active normoactive bowel sounds. Neurological examination showed normal "
                "cranial nerve function, motor strength, and sensory perception. Diagnostic laboratory panels and imaging studies "
                "were reviewed and demonstrated results consistent with the established baseline clinical course. "
                "Therapeutic compliance was discussed at length, and existing lifestyle recommendations regarding aerobic exercise "
                "and nutritional guidelines were reaffirmed. Follow-up is recommended in six months or sooner if symptoms arise. "
                "Additional extensive clinical history was reviewed covering prior surgical interventions, pediatric milestones, "
                "family health lineage, environmental exposures, occupational ergonomics, immunization records, travel advisories, "
                "and preventive health screening protocols across multiple care encounters and clinical disciplines. "
                "All physiological parameters and clinical benchmarks were systematically documented as part of ongoing outpatient clinical surveillance. "
                "Interpretation: Overall clinical presentation is stable with no acute abnormalities or contraindications."
            )

        output_dict = {
            "chief_concern": "Follow-up clinical consultation",
            "diagnoses": diagnoses,
            "key_findings": key_findings,
            "recent_medical_media": media,
            "notable_anomalies": anomalies,
            "narrative_summary": narrative,
        }
        return json.dumps(output_dict, indent=2)

    def shorten(self, original_summary: str, current_word_count: int, system_prompt: str) -> str:
        self.shorten_call_count += 1
        return (
            "The patient presented for comprehensive clinical follow-up. Vital signs and physical exam were stable "
            "with no acute distress. Diagnostic laboratory panels and imaging studies confirmed normal baseline findings. "
            "Interpretation: Stable clinical presentation under ongoing outpatient management."
        )

    def repair_json(self, malformed_json: str, parse_error: str, system_prompt: str) -> str:
        self.repair_call_count += 1
        return json.dumps(
            {
                "chief_concern": "Follow-up clinical consultation",
                "diagnoses": [],
                "key_findings": ["Vital signs stable"],
                "recent_medical_media": [],
                "notable_anomalies": [],
                "narrative_summary": "Repaired clinical narrative summary. Interpretation: Stable findings.",
            }
        )


def get_llm_client() -> BaseLLMClient:
    """Factory creating configured LLM client instance based on Settings."""
    settings = get_settings()
    if settings.llm_provider == "groq":
        return GroqLLMClient()
    elif settings.llm_provider == "mock":
        return MockLLMClient()
    raise LLMConfigurationError(f"Unsupported LLM provider: {settings.llm_provider}")
