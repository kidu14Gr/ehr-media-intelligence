"""Audit log and batch execution models for traceability."""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.enums import ProcessingStatus
from app.schemas.intermediate import IntermediateRecord


class TransformationRecord(BaseModel):
    """Records a single normalization change for audit compliance."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    field_name: str = Field(..., description="Demographic or clinical field modified")
    original_value: Optional[str] = Field(None, description="Raw unnormalized value")
    normalized_value: Optional[str] = Field(None, description="Resulting normalized value")
    reason: str = Field(..., description="Rationale for transformation")


class AuditLogEntry(BaseModel):
    """Per-record audit trail entry capturing what occurred during ingestion."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_id: str = Field(..., description="Unique audit entry ID (UUID)")
    record_id: Optional[str] = Field(None, description="Intermediate record ID if created")
    source_file: str = Field(..., description="Originating filename or stream")
    source_record_id: Optional[str] = Field(None, description="Row number or source ID")
    sanitized_mrn: Optional[str] = Field(None, description="Sanitized MRN")
    facility_id: Optional[str] = Field(None, description="Facility ID if present")
    status: ProcessingStatus = Field(..., description="Outcome status")
    transformations: List[TransformationRecord] = Field(
        default_factory=list, description="List of normalization changes"
    )
    warnings: List[str] = Field(default_factory=list, description="Non-fatal warnings")
    errors: List[str] = Field(default_factory=list, description="Validation or conflict error messages")
    processed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Audit entry timestamp in UTC",
    )


class QuarantinedRecord(BaseModel):
    """Holds unassigned, malformed, or conflicting records for clinical review."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    quarantine_id: str = Field(..., description="Unique quarantine UUID")
    source_file: str = Field(..., description="Origin file name")
    source_record_id: Optional[str] = None
    raw_data: Dict[str, Any] = Field(..., description="Raw unprocessed data payload")
    reason: str = Field(..., description="Explanation of why record was quarantined")
    quarantined_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Quarantine timestamp in UTC",
    )


class IngestionBatchResult(BaseModel):
    """Summary of a completed ingestion pipeline run."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    total_processed: int = Field(..., description="Total raw records encountered")
    successful_records: List[IntermediateRecord] = Field(default_factory=list)
    quarantined_records: List[QuarantinedRecord] = Field(default_factory=list)
    duplicate_count: int = Field(default=0, description="Count of duplicate records ignored")
    audit_logs: List[AuditLogEntry] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime
