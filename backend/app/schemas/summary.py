"""Pydantic v2 schemas for Clinical Summarization (Task 3).

Enforces:
- Separation of documented facts from AI interpretation
- Clinical safety guardrails (flagged/unverified diagnoses recorded)
- Strict word count (< 200 words) with recorded warnings
- Explainable confidence scoring and mandatory medical disclaimer
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClinicalAnomaly(BaseModel):
    """Anomalous clinical finding, abnormal lab result, or alert."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str = Field(description="Description of the anomaly or abnormal finding")
    severity: Literal["low", "moderate", "high", "critical", "unknown"] = Field(
        default="unknown", description="Clinical severity classification"
    )
    source_reference: Optional[str] = Field(
        default=None, description="Document ID or resource reference where anomaly appeared"
    )


class MedicalMediaItem(BaseModel):
    """Metadata and findings for medical imaging or media documents."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    modality: str = Field(description="Media modality: e.g. 'Chest X-Ray', 'CT Scan', 'Ultrasound', 'Document'")
    description: str = Field(description="Description of the study or media record")
    date: Optional[str] = Field(default=None, description="Date study was acquired, if available")
    findings: Optional[str] = Field(default=None, description="Key radiologic or diagnostic findings")


class LLMRawSummaryOutput(BaseModel):
    """Direct JSON structure expected from LLM generation."""

    model_config = ConfigDict(extra="ignore")

    chief_concern: Optional[str] = Field(default=None, description="Primary clinical presentation or visit reason")
    diagnoses: List[str] = Field(default_factory=list, description="Clinical diagnoses extracted from record")
    key_findings: List[str] = Field(default_factory=list, description="Salient clinical findings and exam results")
    recent_medical_media: List[MedicalMediaItem] = Field(default_factory=list, description="Imaging studies and reports")
    notable_anomalies: List[ClinicalAnomaly] = Field(default_factory=list, description="Abnormal values or alerts")
    narrative_summary: str = Field(description="Synthesized clinical narrative targeting ~150 words")


class StructuredClinicalSummary(BaseModel):
    """Validated, production-grade structured clinical summary."""

    model_config = ConfigDict(extra="forbid")

    summary_id: str = Field(description="Unique summary identifier: sum-{uuid}")
    patient_id: str = Field(description="Patient identifier: pat-{record_id}")
    content_hash: str = Field(description="SHA-256 hash of patient input clinical records")
    prompt_version: str = Field(description="Prompt template version used for generation")
    model_used: str = Field(description="LLM model identifier used for generation")
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC generation timestamp",
    )

    # Documented Facts vs AI Interpretation
    chief_concern: Optional[str] = Field(default=None, description="Primary clinical presentation or reason for visit")
    documented_diagnoses: List[str] = Field(
        default_factory=list,
        description="Clinical diagnoses verified against source text (documented facts)",
    )
    unverified_or_flagged_diagnoses: List[str] = Field(
        default_factory=list,
        description="Diagnoses suggested by LLM that were NOT found verbatim in source text (isolated for safety)",
    )
    key_findings: List[str] = Field(
        default_factory=list,
        description="Key laboratory, physical exam, or clinical findings",
    )
    recent_medical_media: List[MedicalMediaItem] = Field(
        default_factory=list,
        description="Recent diagnostic imaging or media records",
    )
    notable_anomalies: List[ClinicalAnomaly] = Field(
        default_factory=list,
        description="Notable anomalies, abnormal lab results, or critical alerts",
    )

    # Narrative Summary (< 200 words)
    narrative_summary: str = Field(description="Synthesized clinical narrative strictly under 200 words")
    word_count: int = Field(description="Word count of the narrative summary")
    word_count_warning: Optional[str] = Field(
        default=None,
        description="Recorded warning if narrative required LLM re-shortening or sentence truncation",
    )

    # Confidence and Safety
    confidence_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Deterministic confidence score reflecting source data completeness",
    )
    confidence_reasons: List[str] = Field(
        default_factory=list,
        description="Transparent explanations justifying the confidence score",
    )
    disclaimer: str = Field(
        default=(
            "AI-GENERATED CLINICAL SUMMARY — FOR INFORMATIONAL ASSISTANCE ONLY. "
            "This summary was synthesized by an artificial intelligence system and must not be used as a "
            "substitute for direct medical review or professional clinical judgment. All diagnoses, findings, "
            "and clinical information should be verified against original source medical documentation."
        ),
        description="Mandatory clinical safety disclaimer",
    )
    is_cached: bool = Field(default=False, description="Whether summary was retrieved from cache")

    @field_validator("narrative_summary")
    @classmethod
    def validate_narrative_word_count(cls, v: str) -> str:
        words = v.strip().split()
        count = len(words)
        if count > 200:
            raise ValueError(f"Narrative summary exceeds strict 200-word limit ({count} words).")
        return v
