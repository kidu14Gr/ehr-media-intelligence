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


class ClinicalSummaryError(IngestionError):
    """Base exception for clinical summarization errors."""
    pass


class LLMConfigurationError(ClinicalSummaryError):
    """Raised when LLM configuration or credentials are missing or invalid."""
    pass


class LLMAPIError(ClinicalSummaryError):
    """Raised when an external LLM API returns an unrecoverable error."""
    pass


class LLMClientError(LLMAPIError):
    """Raised when an embedding or LLM client request fails."""
    pass


class LLMRateLimitError(LLMAPIError):
    """Raised when an LLM provider returns HTTP 429 rate limit."""
    pass


class LLMTimeoutError(LLMAPIError):
    """Raised when an LLM request exceeds configured timeout."""
    pass


class SummaryParsingError(ClinicalSummaryError):
    """Raised when an LLM response cannot be parsed or validated against the summary schema."""
    pass


class SummaryWordCountError(ClinicalSummaryError):
    """Raised when a summary fails strict word count validation (< 200 words)."""
    pass


class SummaryCacheError(ClinicalSummaryError):
    """Raised when storing or retrieving summaries from cache fails."""
    pass


class SearchError(IngestionError):
    """Base exception for semantic search and vector indexing errors."""
    pass


class IndexBackendMismatchError(SearchError):
    """Raised when active embedding backend differs from the one that built the index."""
    pass


class PatientNotFoundError(SearchError):
    """Raised when requested patient ID is not found in database."""
    pass
