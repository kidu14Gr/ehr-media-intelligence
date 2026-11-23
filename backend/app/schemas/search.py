"""Pydantic schemas for clinical semantic search and patient detail drawer."""
from typing import Any, Optional
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Semantic search query request payload."""

    query: str = Field(..., min_length=1, description="Free-text clinical query")
    resource_type: Optional[str] = Field(
        default=None,
        description="Filter by FHIR resource type ('DocumentReference', 'DiagnosticReport', 'Summary')",
    )
    start_date: Optional[str] = Field(
        default=None,
        description="Filter records on or after this ISO date (YYYY-MM-DD)",
    )
    end_date: Optional[str] = Field(
        default=None,
        description="Filter records on or before this ISO date (YYYY-MM-DD)",
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum number of patient matches to return (default 5)",
    )
    min_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum relevance score threshold (defaults to MIN_RELEVANCE_SCORE from config)",
    )


class PatientSearchResult(BaseModel):
    """Patient-level search match aggregating most relevant clinical excerpt."""

    patient_id: str
    patient_name: str
    mrn: str
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    record_date: Optional[str] = None
    resource_type: str
    score: float = Field(..., description="Cosine similarity score (0.0 to 1.0)")
    summary_snippet: str = Field(..., description="Excerpt from matched document or summary")


class SearchResponse(BaseModel):
    """Response containing ranked patient search results."""

    query: str
    total_patients: int
    results: list[PatientSearchResult]


class PatientProfileDemographics(BaseModel):
    """Demographic overview for patient detail drawer."""

    patient_id: str
    first_name: str
    last_name: str
    gender: str
    date_of_birth: Optional[str] = None
    mrn: Optional[str] = None
    facility_id: Optional[str] = None


class LinkedFHIRResource(BaseModel):
    """FHIR resource linked to patient profile."""

    resource_id: str
    resource_type: str
    effective_date: Optional[str] = None
    encounter_id: Optional[str] = None
    fhir_json: dict[str, Any]


class PatientDetailResponse(BaseModel):
    """Complete patient profile for clinical drawer view in Task 5."""

    patient: PatientProfileDemographics
    summary: Optional[dict[str, Any]] = None
    linked_resources: list[LinkedFHIRResource]
