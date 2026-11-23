"""Custom domain exceptions for EHR platform."""

class IngestionError(Exception):
    """Base exception for all platform errors."""
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


class FHIRConversionError(IngestionError):
    """Raised when an intermediate record cannot be transformed into FHIR R4."""
    pass


class FHIRValidationError(IngestionError):
    """Raised when a generated FHIR bundle or resource violates specification or referential integrity."""
    pass


class FHIRStorageError(IngestionError):
    """Raised when persisting FHIR resources to SQLite or JSON fails."""
    pass
