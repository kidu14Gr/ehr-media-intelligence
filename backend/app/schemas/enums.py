"""Canonical enums for the EHR Media Intelligence Platform."""
from enum import Enum


class Gender(str, Enum):
    """Normalized clinical gender according to HL7 administrative gender concepts."""
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class DocumentType(str, Enum):
    """Supported clinical document categories for Task 1 and downstream Task 2 FHIR mapping."""
    LAB_REPORT = "LAB_REPORT"
    IMAGING = "IMAGING"
    DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY"
    CLINICAL_NOTE = "CLINICAL_NOTE"
    OTHER = "OTHER"


class ProcessingStatus(str, Enum):
    """Outcome status for each processed record in the audit trail."""
    SUCCESS = "SUCCESS"
    NORMALIZED_WITH_WARNINGS = "NORMALIZED_WITH_WARNINGS"
    DUPLICATE_IGNORED = "DUPLICATE_IGNORED"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    QUARANTINED = "QUARANTINED"
