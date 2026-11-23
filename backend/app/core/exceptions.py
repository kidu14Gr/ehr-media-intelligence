"""Custom domain exceptions for EHR ingestion pipeline."""

class IngestionError(Exception):
    """Base exception for all ingestion-related errors."""
    pass


class SchemaValidationError(IngestionError):
    """Raised when record data fails intermediate schema validation."""
    pass


class DateParsingError(IngestionError):
    """Raised when a date cannot be parsed or is ambiguous."""
    pass


class DuplicateRecordError(IngestionError):
    """Raised when an incoming record is identified as an exact duplicate."""
    pass


class IdentityConflictError(IngestionError):
    """Raised when incoming record has conflicting patient identifiers."""
    pass


class UnassignedDocumentError(IngestionError):
    """Raised when clinical narrative document cannot be linked to any patient."""
    pass
