"""Clinical document and summary indexer for vector search.

Extracts clinical narratives from FHIR DocumentReference and DiagnosticReport resources,
as well as AI-generated patient summaries. Strips direct PHI headers (names/MRNs) so that
only clinical observations and narratives are embedded. Upserts idempotently into Chroma.
"""
import base64
import hashlib
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

from app.core.config import Settings, get_settings
from app.search.embeddings import BaseEmbeddingClient, get_embedding_client
from app.search.vector_store import VectorStoreManager

logger = logging.getLogger(__name__)


def sanitize_clinical_text(text: str) -> str:
    """Strips demographic header lines (Patient:, MRN:, DOB:, Facility:), system IDs, and raw timestamps."""
    from app.ai.sanitizer import sanitize_clinical_identifiers_and_timestamps
    clean_lines = []
    for line in text.splitlines():
        stripped = line.strip().lower()
        if any(
            stripped.startswith(prefix)
            for prefix in ("patient:", "mrn:", "dob:", "facility:", "date of birth:")
        ):
            continue
        clean_lines.append(line)
    text_cleaned = "\n".join(clean_lines).strip()
    return sanitize_clinical_identifiers_and_timestamps(text_cleaned) or ""


def parse_date_to_int(effective_date: Optional[str]) -> tuple[bool, str, int]:
    """Parses date string into (has_date, date_str_YYYY_MM_DD, date_int_YYYYMMDD).

    Returns (False, "", 0) for undated records.
    """
    if not effective_date or not effective_date.strip():
        return False, "", 0

    clean = effective_date.strip()
    date_part = clean[:10]  # Extracts YYYY-MM-DD
    parts = date_part.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        date_int = year * 10000 + month * 100 + day
        return True, date_part, date_int

    return False, "", 0


def extract_sentence_aware_snippet(text: str, min_chars: int = 150) -> str:
    """Extracts sentence-aware excerpt of at least min_chars without cutting mid-sentence.

    Sanitizes system identifiers and raw timestamps per clinical privacy rules.
    """
    if not text:
        return ""
    from app.ai.sanitizer import sanitize_clinical_identifiers_and_timestamps
    sanitized = sanitize_clinical_identifiers_and_timestamps(text) or ""
    clean = sanitized.replace("\n", " ").strip()
    clean = re.sub(r"\s+", " ", clean)
    if len(clean) <= min_chars:
        return clean

    sentences = re.split(r"(?<=[.!?])\s+", clean)
    snippet_parts = []
    current_len = 0
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        snippet_parts.append(s)
        current_len += len(s) + 1
        if current_len >= min_chars:
            break

    result = " ".join(snippet_parts).strip()
    return result if result else clean[:min_chars]


class ClinicalIndexer:
    """Builds and updates persistent vector index from FHIR resources and AI summaries."""

    def __init__(
        self,
        vector_store: Optional[VectorStoreManager] = None,
        embedding_client: Optional[BaseEmbeddingClient] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._vector_store = vector_store or VectorStoreManager(settings=self._settings)
        self._embedder = embedding_client or get_embedding_client(settings=self._settings)
        self._db_path = self._settings.database_path

    @property
    def vector_store(self) -> VectorStoreManager:
        return self._vector_store

    @property
    def embedder(self) -> BaseEmbeddingClient:
        return self._embedder

    def collect_fhir_records(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        """Collects DocumentReference and DiagnosticReport records from SQLite."""
        cursor = conn.cursor()
        cursor.execute("""
            SELECT resource_id, resource_type, patient_id, effective_date, fhir_json
            FROM fhir_resources
            WHERE resource_type IN ('DocumentReference', 'DiagnosticReport')
            ORDER BY resource_id ASC
        """)
        records = []
        for row in cursor.fetchall():
            res_id, res_type, pat_id, eff_date, raw_json = row
            try:
                fhir_obj = json.loads(raw_json)
            except Exception:
                continue

            clinical_text = ""
            if res_type == "DocumentReference":
                content = fhir_obj.get("content", [])
                if content and isinstance(content, list):
                    attachment = content[0].get("attachment", {})
                    b64_data = attachment.get("data")
                    if b64_data:
                        try:
                            clinical_text = base64.b64decode(b64_data).decode("utf-8")
                        except Exception:
                            clinical_text = ""
                if not clinical_text:
                    clinical_text = fhir_obj.get("description", "")
            elif res_type == "DiagnosticReport":
                conclusion = fhir_obj.get("conclusion", "")
                presented = fhir_obj.get("presentedForm", [])
                presented_text = ""
                if presented and isinstance(presented, list):
                    p_att = presented[0]
                    if isinstance(p_att, dict) and "data" in p_att and p_att["data"]:
                        try:
                            presented_text = base64.b64decode(p_att["data"]).decode("utf-8")
                        except Exception:
                            presented_text = ""
                cat_display = ""
                category = fhir_obj.get("category", [])
                if category and isinstance(category, list) and category[0].get("coding"):
                    cat_display = category[0]["coding"][0].get("display", "")
                clean_text = ""
                if "text" in fhir_obj and "div" in fhir_obj["text"]:
                    clean_text = re.sub(r"<[^>]+>", " ", fhir_obj["text"]["div"]).strip()
                body_text = conclusion or presented_text or clean_text
                clinical_text = f"{cat_display} Report: {body_text}".strip()

            clinical_text = sanitize_clinical_text(clinical_text)
            if not clinical_text:
                continue

            has_date, date_str, date_int = parse_date_to_int(eff_date)
            content_hash = hashlib.sha256(clinical_text.encode("utf-8")).hexdigest()
            snippet = clinical_text[:180].replace("\n", " ").strip()

            records.append({
                "id": f"res-{res_id}",
                "patient_id": pat_id,
                "resource_id": res_id,
                "resource_type": res_type,
                "text": clinical_text,
                "record_date": date_str,
                "record_date_int": date_int,
                "has_date": has_date,
                "content_hash": content_hash,
                "summary_snippet": snippet,
            })

        return records

    def collect_summary_records(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        """Collects the latest patient-level AI summary per patient from SQLite."""
        cursor = conn.cursor()
        cursor.execute("""
            SELECT cs.summary_id, cs.patient_id, cs.chief_concern, cs.narrative_summary,
                   cs.summary_json, cs.created_at
            FROM clinical_summaries cs
            INNER JOIN (
                SELECT patient_id, MAX(created_at) as max_created
                FROM clinical_summaries
                GROUP BY patient_id
            ) latest ON cs.patient_id = latest.patient_id AND cs.created_at = latest.max_created
            ORDER BY cs.patient_id ASC
        """)
        records = []
        for row in cursor.fetchall():
            sum_id, pat_id, chief_concern, narrative, summary_json_str, created_at = row
            try:
                sum_obj = json.loads(summary_json_str)
            except Exception:
                sum_obj = {}

            findings = sum_obj.get("key_findings", [])
            findings_text = "; ".join(findings) if findings else ""
            concern_text = f"Chief Concern: {chief_concern}" if chief_concern else ""
            text = f"{concern_text}\n{narrative}\nKey Findings: {findings_text}".strip()
            text = sanitize_clinical_text(text)
            if not text:
                continue

            # Query latest source-document date for this patient (never summary creation date)
            date_cursor = conn.cursor()
            date_cursor.execute("""
                SELECT MAX(effective_date)
                FROM fhir_resources
                WHERE patient_id = ?
                  AND effective_date IS NOT NULL
                  AND TRIM(effective_date) != ''
            """, (pat_id,))
            date_row = date_cursor.fetchone()
            latest_eff_date = date_row[0] if date_row and date_row[0] else None

            has_date, date_str, date_int = parse_date_to_int(latest_eff_date)
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            snippet = extract_sentence_aware_snippet(narrative or text, min_chars=150)

            records.append({
                "id": f"sum-{pat_id}",
                "patient_id": pat_id,
                "resource_id": sum_id,
                "resource_type": "Summary",
                "text": text,
                "record_date": date_str,
                "record_date_int": date_int,
                "has_date": has_date,
                "content_hash": content_hash,
                "summary_snippet": snippet,
            })

        return records

    def build_index(self, force_rebuild: bool = False) -> dict[str, Any]:
        """Indexes all FHIR clinical documents and patient AI summaries into Chroma.

        Idempotent: Re-running upserts identical records without duplication.
        Removes stale vectors if source records were deleted.
        """
        logger.info(
            "Building clinical vector index using backend '%s' (%s)",
            self._embedder.backend_name,
            self._embedder.model_name,
        )

        if force_rebuild:
            self._vector_store.reset_collection()

        if not self._db_path.exists():
            raise FileNotFoundError(f"Database file not found at: {self._db_path}")

        conn = sqlite3.connect(self._db_path)
        try:
            fhir_records = self.collect_fhir_records(conn)
            summary_records = self.collect_summary_records(conn)
        finally:
            conn.close()

        all_records = fhir_records + summary_records
        if not all_records:
            logger.warning("No clinical records or summaries found to index.")
            return {"total_indexed": 0, "upserted": 0, "skipped": 0, "stale_removed": 0, "fhir_count": 0, "summary_count": 0}

        # Check existing vectors and content hashes to skip re-embedding unchanged records
        existing = self._vector_store.collection.get(include=["metadatas"])
        existing_ids = existing.get("ids", [])
        existing_metas = existing.get("metadatas", [])
        existing_hashes = {
            id_: (meta.get("content_hash") if meta else None)
            for id_, meta in zip(existing_ids, existing_metas)
        }

        records_to_embed = [
            r for r in all_records
            if existing_hashes.get(r["id"]) != r["content_hash"]
        ]

        upserted_count = 0
        if records_to_embed:
            logger.info(
                "Embedding %d new or modified clinical records (skipping %d unchanged)...",
                len(records_to_embed),
                len(all_records) - len(records_to_embed),
            )
            texts = [r["text"] for r in records_to_embed]
            embeddings = self._embedder.embed_texts(texts)
            metadatas = [
                {
                    "patient_id": r["patient_id"],
                    "resource_id": r["resource_id"],
                    "resource_type": r["resource_type"],
                    "record_date": r["record_date"],
                    "record_date_int": r["record_date_int"],
                    "has_date": r["has_date"],
                    "content_hash": r["content_hash"],
                    "summary_snippet": r["summary_snippet"],
                }
                for r in records_to_embed
            ]
            upserted_count = self._vector_store.upsert_records(
                ids=[r["id"] for r in records_to_embed],
                embeddings=embeddings,
                metadatas=metadatas,
                documents=texts,
            )
        else:
            logger.info("All %d records have matching content hashes. Skipping re-embedding.", len(all_records))

        # Prune stale records
        active_ids = {r["id"] for r in all_records}
        stale_count = self._vector_store.remove_stale_records(active_ids=active_ids)

        # Record index manifest
        self._vector_store.save_manifest(
            backend_name=self._embedder.backend_name,
            model_name=self._embedder.model_name,
            dimension=self._embedder.dimension,
            document_count=self._vector_store.count(),
        )

        metrics = {
            "total_indexed": self._vector_store.count(),
            "upserted": upserted_count,
            "skipped": len(all_records) - len(records_to_embed),
            "stale_removed": stale_count,
            "fhir_count": len(fhir_records),
            "summary_count": len(summary_records),
            "backend": self._embedder.backend_name,
            "model": self._embedder.model_name,
        }
        logger.info("Index build completed successfully: %s", metrics)
        return metrics
