"""Validation report models for FHIR R4B bundles and resources."""
from datetime import datetime, timezone
from typing import Dict, List
from pydantic import BaseModel, ConfigDict, Field


class ValidationIssue(BaseModel):
    """Specific structural or referential issue found during FHIR validation."""
    model_config = ConfigDict(frozen=True)

    severity: str = Field(..., description="ERROR, WARNING, or INFO")
    resource_id: str = Field(..., description="Logical ID or relative reference of the resource")
    resource_type: str = Field(..., description="FHIR resource type (e.g. Patient, DocumentReference)")
    field_path: str = Field(..., description="JSON path or element where issue was detected")
    message: str = Field(..., description="Detailed description of the validation issue")


class FHIRValidationReport(BaseModel):
    """Validation report summarizing resource structure and referential integrity."""
    model_config = ConfigDict(frozen=True)

    is_valid: bool = Field(..., description="True if no ERROR severity issues were found")
    total_bundles: int = Field(..., description="Total bundles inspected")
    total_resources: int = Field(..., description="Total individual FHIR resources inspected")
    resource_counts: Dict[str, int] = Field(
        default_factory=dict,
        description="Count of resources grouped by resourceType",
    )
    issues: List[ValidationIssue] = Field(default_factory=list, description="List of detected validation issues")
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Report generation timestamp in UTC",
    )
    compliance_note: str = Field(
        default=(
            "Structural validation performed against HL7 FHIR R4B specification models. "
            "Structural validation verifies type constraints and local referential integrity "
            "but does not guarantee complete external US Core profile conformance or remote "
            "terminology server code validation."
        ),
        description="Disclaimer on scope of local structural validation",
    )
