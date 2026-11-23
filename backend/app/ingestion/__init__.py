"""Ingestion package for EHR Media Intelligence Platform."""
from app.ingestion.audit import AuditLogger
from app.ingestion.dedup import PatientRegistry, compute_document_hash, DedupResult
from app.ingestion.normalizers import (
    normalize_date,
    normalize_datetime,
    normalize_gender,
    normalize_mrn,
    DateNormalizationResult,
    DateTimeNormalizationResult,
    GenderNormalizationResult,
    MRNNormalizationResult,
)
from app.ingestion.parsers import (
    BaseParser,
    JSONParser,
    CSVParser,
    PlainTextParser,
    RawClinicalRecord,
    get_parser_for_file,
)
from app.ingestion.pipeline import IngestionPipeline

__all__ = [
    "AuditLogger",
    "PatientRegistry",
    "compute_document_hash",
    "DedupResult",
    "normalize_date",
    "normalize_datetime",
    "normalize_gender",
    "normalize_mrn",
    "DateNormalizationResult",
    "DateTimeNormalizationResult",
    "GenderNormalizationResult",
    "MRNNormalizationResult",
    "BaseParser",
    "JSONParser",
    "CSVParser",
    "PlainTextParser",
    "RawClinicalRecord",
    "get_parser_for_file",
    "IngestionPipeline",
]
