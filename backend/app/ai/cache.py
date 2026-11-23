"""Dual cache engine for Clinical Summaries (SQLite primary, JSON secondary).

Cache key formula:
  key = content_hash + ":" + PROMPT_VERSION + ":" + model_name

Guarantees:
- Zero redundant LLM API calls on identical clinical records
- Automatic invalidation when patient clinical facts, prompt template, or model change
- Idempotent SQLite upserts via ON CONFLICT clauses
- Secondary JSON file persistence in storage/cache/summaries/
"""
import json
import logging
from pathlib import Path
import re
import sqlite3
from typing import Optional

from app.core.config import get_settings
from app.core.exceptions import SummaryCacheError
from app.schemas.summary import StructuredClinicalSummary

logger = logging.getLogger("ehr.cache")


class ClinicalSummaryCache:
    """Dual-layer cache engine: SQLite primary, JSON secondary."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
    ) -> None:
        settings = get_settings()
        self.db_path = db_path or settings.database_path
        self.cache_dir = cache_dir or settings.summary_cache_dir
        self._init_cache()

    def _init_cache(self) -> None:
        """Initializes SQLite schema and cache directory."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS clinical_summaries (
                    summary_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    cache_key TEXT NOT NULL UNIQUE,
                    content_hash TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    model_used TEXT NOT NULL,
                    chief_concern TEXT,
                    narrative_summary TEXT NOT NULL,
                    word_count INTEGER NOT NULL,
                    confidence_score REAL NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES patients(patient_id)
                );
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_summaries_patient ON clinical_summaries(patient_id);"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_summaries_cache_key ON clinical_summaries(cache_key);"
            )
            conn.commit()

    @staticmethod
    def build_cache_key(content_hash: str, prompt_version: str, model_name: str) -> str:
        """Constructs cache key from patient content hash, prompt version, and model."""
        return f"{content_hash}:{prompt_version}:{model_name}"

    def _safe_filename(self, patient_id: str, cache_key: str) -> str:
        safe_key = re.sub(r"[^a-zA-Z0-9_\-]", "_", cache_key)
        return f"{patient_id}_{safe_key[:32]}.json"

    def get(self, cache_key: str) -> Optional[StructuredClinicalSummary]:
        """Retrieves summary from SQLite primary, falling back to JSON secondary."""
        # 1. Primary: SQLite
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT summary_json FROM clinical_summaries WHERE cache_key = ?;",
                    (cache_key,),
                )
                row = cursor.fetchone()
                if row:
                    data = json.loads(row[0])
                    summary = StructuredClinicalSummary.model_validate(data)
                    summary.is_cached = True
                    logger.info("Cache HIT (SQLite primary) [key=%s]", cache_key[:16])
                    return summary
        except Exception as e:
            logger.warning("Error reading from SQLite cache: %s", e)

        # 2. Secondary: JSON files
        try:
            # Search cache_dir for matching cache_key inside files
            for json_file in self.cache_dir.glob("*.json"):
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                    file_key = self.build_cache_key(
                        content_hash=data.get("content_hash", ""),
                        prompt_version=data.get("prompt_version", ""),
                        model_name=data.get("model_used", ""),
                    )
                    if file_key == cache_key:
                        summary = StructuredClinicalSummary.model_validate(data)
                        summary.is_cached = True
                        # Backfill into SQLite
                        self._upsert_sqlite(summary, cache_key)
                        logger.info("Cache HIT (JSON secondary) [key=%s]", cache_key[:16])
                        return summary
                except Exception:
                    continue
        except Exception as e:
            logger.warning("Error reading from JSON cache directory: %s", e)

        logger.info("Cache MISS [key=%s]", cache_key[:16])
        return None

    def store(self, summary: StructuredClinicalSummary, cache_key: str) -> None:
        """Persists summary to both SQLite primary and JSON secondary."""
        try:
            self._upsert_sqlite(summary, cache_key)
        except Exception as e:
            raise SummaryCacheError(f"Failed to persist summary in SQLite: {e}") from e

        try:
            filename = self._safe_filename(summary.patient_id, cache_key)
            out_file = self.cache_dir / filename
            out_file.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to persist summary to JSON cache file: %s", e)

    def _upsert_sqlite(self, summary: StructuredClinicalSummary, cache_key: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            summary_raw = summary.model_dump_json()
            cursor.execute(
                """
                INSERT INTO clinical_summaries (
                    summary_id, patient_id, cache_key, content_hash, prompt_version,
                    model_used, chief_concern, narrative_summary, word_count,
                    confidence_score, summary_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    summary_id=excluded.summary_id,
                    narrative_summary=excluded.narrative_summary,
                    word_count=excluded.word_count,
                    confidence_score=excluded.confidence_score,
                    summary_json=excluded.summary_json,
                    created_at=excluded.created_at;
                """,
                (
                    summary.summary_id,
                    summary.patient_id,
                    cache_key,
                    summary.content_hash,
                    summary.prompt_version,
                    summary.model_used,
                    summary.chief_concern,
                    summary.narrative_summary,
                    summary.word_count,
                    summary.confidence_score,
                    summary_raw,
                    summary.generated_at.isoformat(),
                ),
            )
            conn.commit()
