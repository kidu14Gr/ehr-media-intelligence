"""Intermediate representation Pydantic v2 models.

Completely decoupled from FHIR schemas; acts as the stable contract for Task 1 ingestion
and input to Task 2 FHIR conversion.
"""
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.schemas.enums import Gender, DocumentType


class PatientDemographics(BaseModel):
    """Validated patient demographics model."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    mrn: str = Field(..., min_length=1, description="Sanitized Medical Record Number")
    original_mrn: Optional[str] = Field(None, description="Original raw MRN value before cleaning")
    facility_id: Optional[str] = Field(None, description="Originating hospital/facility identifier")
    first_name: str = Field(..., min_length=1, description="Given name")
    last_name: str = Field(..., min_length=1, description="Family name")
    date_of_birth: date = Field(..., description="Normalized birth date (YYYY-MM-DD)")
    gender: Gender = Field(default=Gender.UNKNOWN, description="Canonical normalized gender")
    original_gender: Optional[str] = Field(None, description="Raw gender string as received")
    national_id: Optional[str] = Field(None, description="National or government identifier")
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None

    @property
    def identity_key(self) -> Tuple[Optional[str], str]:
        """Composite patient identity key (facility_id, MRN)."""
        return (self.facility_id, self.mrn)

    @field_validator("date_of_birth")
    @classmethod
    def validate_dob_sanity(cls, v: date) -> date:
        """Ensure date of birth is not in the future and does not exceed human lifespan."""
        today = date.today()
        if v > today:
            raise ValueError(f"Date of birth {v} cannot be in the future")
        if (today.year - v.year) > 130:
            raise ValueError(f"Date of birth {v} exceeds reasonable human lifespan (>130 years)")
        return v


class ClinicalDocumentRecord(BaseModel):
    """Clinical document, report, or unstructured note linked to a patient."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str = Field(..., min_length=1, description="Unique document ID (UUID or deterministic)")
    encounter_id: Optional[str] = Field(None, description="Encounter or visit identifier")
    document_type: DocumentType = Field(default=DocumentType.CLINICAL_NOTE)
    effective_date: Optional[datetime] = Field(default=None, description="Encounter or observation timestamp in timezone-aware UTC, or None if not provided")
    title: str = Field(..., min_length=1, description="Document title reflecting clinical document type or source title")
    text_content: str = Field(..., min_length=1, description="Original preserved clinical narrative")
    author: Optional[str] = None
    facility: Optional[str] = None
    status: str = Field(default="final", description="Preliminary, final, amended, etc.")
    structured_data: Dict[str, Any] = Field(default_factory=dict, description="Parsed clinical key-value items")
    diagnoses: List[str] = Field(default_factory=list, description="Extracted diagnostic impressions")
    media_mime_type: Optional[str] = None
    media_file_path: Optional[str] = None
    content_hash: str = Field(..., description="SHA-256 hash of text content and metadata for deduplication")

    @field_validator("effective_date")
    @classmethod
    def ensure_timezone_aware(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Enforce timezone-aware UTC timestamps when present."""
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class SourceMetadata(BaseModel):
    """Provenance and ingestion metadata for the source record."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_file: str = Field(..., min_length=1, description="Origin file name or input source")
    source_format: str = Field(..., min_length=1, description="JSON, CSV, or TXT")
    source_record_id: Optional[str] = Field(None, description="Source row index or original ID")
    ingested_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Ingestion timestamp in timezone-aware UTC",
    )


class IntermediateRecord(BaseModel):
    """Complete, validated patient bundle representing all clinical records for a patient.
    
    This is the immutable domain entity passed from Task 1 into Task 2.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str = Field(..., min_length=1, description="Deterministic patient record UUID")
    patient: PatientDemographics
    documents: List[ClinicalDocumentRecord] = Field(default_factory=list)
    metadata: SourceMetadata

    @model_validator(mode="after")
    def validate_document_dates(self) -> "IntermediateRecord":
        """Verify that document dates do not precede the patient's birth date."""
        for doc in self.documents:
            if doc.effective_date is not None and doc.effective_date.date() < self.patient.date_of_birth:
                raise ValueError(
                    f"Document effective date {doc.effective_date.date()} cannot precede "
                    f"patient birth date {self.patient.date_of_birth}"
                )
        return self
