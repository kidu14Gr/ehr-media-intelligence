"""Audit logging mechanism for the EHR ingestion pipeline.

Provides structured per-record traceability of:
- Field normalizations and transformations
- Non-fatal warnings (e.g. ambiguous dates, non-standard genders)
- Fatal validation, conflict, or unassigned document errors
- Ingestion timestamps and processing outcomes
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from app.schemas.enums import ProcessingStatus
from app.schemas.audit import AuditLogEntry, TransformationRecord


class AuditLogger:
    """Collects and manages per-record audit log entries."""

    def __init__(self) -> None:
        self._entries: List[AuditLogEntry] = []

    def log(
        self,
        source_file: str,
        status: ProcessingStatus,
        source_record_id: Optional[str] = None,
        record_id: Optional[str] = None,
        sanitized_mrn: Optional[str] = None,
        facility_id: Optional[str] = None,
        transformations: Optional[List[TransformationRecord]] = None,
        warnings: Optional[List[str]] = None,
        errors: Optional[List[str]] = None,
    ) -> AuditLogEntry:
        """Appends a new immutable AuditLogEntry."""
        entry = AuditLogEntry(
            audit_id=f"AUD-{uuid.uuid4().hex[:10].upper()}",
            record_id=record_id,
            source_file=source_file,
            source_record_id=source_record_id,
            sanitized_mrn=sanitized_mrn,
            facility_id=facility_id,
            status=status,
            transformations=transformations or [],
            warnings=warnings or [],
            errors=errors or [],
            processed_at=datetime.now(timezone.utc),
        )
        self._entries.append(entry)
        return entry

    def get_entries(self) -> List[AuditLogEntry]:
        """Returns all recorded audit entries."""
        return list(self._entries)

    def to_dict_list(self) -> List[Dict[str, Any]]:
        """Serializes entries to JSON-compatible dictionaries."""
        return [entry.model_dump(mode="json") for entry in self._entries]

    def clear(self) -> None:
        """Resets the audit log buffer."""
        self._entries.clear()
