"""Clinical semantic search service grouping by patient and enforcing date filters.

Handles query embedding, vector similarity scoring, Chroma metadata filtering,
and patient-level aggregation with SQLite demographic and summary enrichment.
"""
import logging
import sqlite3
from typing import Any, Optional

from app.core.config import Settings, get_settings
from app.core.exceptions import IndexBackendMismatchError, PatientNotFoundError
from app.schemas.search import (
    LinkedFHIRResource,
    PatientDetailResponse,
    PatientProfileDemographics,
    PatientSearchResult,
    SearchRequest,
    SearchResponse,
)
from app.search.embeddings import BaseEmbeddingClient, get_embedding_client
from app.search.indexer import extract_sentence_aware_snippet, parse_date_to_int
from app.search.vector_store import VectorStoreManager

logger = logging.getLogger(__name__)


class ClinicalSearchService:
    """Orchestrates clinical semantic search, filtering, and patient detail retrieval."""

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

    def _build_chroma_filter(
        self,
        resource_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Constructs Chroma metadata filter enforcing undated exclusion only when date filter is present."""
        conditions: list[dict[str, Any]] = []

        if resource_type:
            conditions.append({"resource_type": {"$eq": resource_type}})

        has_date_filter = bool(start_date or end_date)
        if has_date_filter:
            # Undated records are strictly excluded when date filtering is requested
            conditions.append({"has_date": {"$eq": True}})

            if start_date:
                _, _, start_int = parse_date_to_int(start_date)
                if start_int > 0:
                    conditions.append({"record_date_int": {"$gte": start_int}})

            if end_date:
                _, _, end_int = parse_date_to_int(end_date)
                if end_int > 0:
                    conditions.append({"record_date_int": {"$lte": end_int}})

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def _get_patient_metadata_cache(self, conn: sqlite3.Connection) -> tuple[dict[str, dict], dict[str, str]]:
        """Preloads patient demographics and AI summary snippets for rapid enrichment."""
        cursor = conn.cursor()

        # Demographics
        cursor.execute("SELECT patient_id, first_name, last_name, mrn FROM patients")
        patients_map = {}
        for row in cursor.fetchall():
            pat_id, first, last, mrn = row
            patients_map[pat_id] = {
                "name": f"{first} {last}".strip(),
                "mrn": mrn or "Unknown",
            }

        # Cached summaries
        cursor.execute("SELECT patient_id, narrative_summary FROM clinical_summaries")
        summaries_map = {}
        for row in cursor.fetchall():
            pat_id, narrative = row
            summaries_map[pat_id] = narrative or ""

        return patients_map, summaries_map

    def search(self, request: SearchRequest) -> SearchResponse:
        """Executes semantic search, applies filters, and returns top patients by relevance."""
        # 1. Enforce embedding backend compatibility
        self._vector_store.verify_backend_compatibility(
            expected_backend=self._embedder.backend_name,
            expected_model=self._embedder.model_name,
        )

        # 2. Embed user query
        query_vector = self._embedder.embed_query(request.query)

        # 3. Construct filter & query Chroma
        where_filter = self._build_chroma_filter(
            resource_type=request.resource_type,
            start_date=request.start_date,
            end_date=request.end_date,
        )

        raw_results = self._vector_store.query_vectors(
            query_embedding=query_vector,
            n_results=50,  # Fetch ample candidate chunks for patient grouping
            where=where_filter,
        )

        # 4. Group chunks by patient taking best score each
        metadatas = raw_results.get("metadatas", [[]])[0]
        distances = raw_results.get("distances", [[]])[0]

        has_date_filter = bool(request.start_date or request.end_date)
        start_int = parse_date_to_int(request.start_date)[2] if request.start_date else 0
        end_int = parse_date_to_int(request.end_date)[2] if request.end_date else 99999999

        min_score_threshold = (
            request.min_score
            if request.min_score is not None
            else self._settings.min_relevance_score
        )

        patient_best_matches: dict[str, dict[str, Any]] = {}

        for meta, dist in zip(metadatas, distances):
            pat_id = meta.get("patient_id")
            if not pat_id:
                continue

            # Defensive post-filter verification
            meta_has_date = bool(meta.get("has_date"))
            meta_date_int = int(meta.get("record_date_int", 0))

            if has_date_filter:
                if not meta_has_date:
                    continue
                if start_int > 0 and meta_date_int < start_int:
                    continue
                if end_int < 99999999 and meta_date_int > end_int:
                    continue

            # Cosine similarity: score = max(0.0, min(1.0, 1.0 - distance))
            score = max(0.0, min(1.0, 1.0 - float(dist)))

            # Drop results below minimum relevance score threshold
            if score < min_score_threshold:
                continue

            if pat_id not in patient_best_matches or score > patient_best_matches[pat_id]["score"]:
                patient_best_matches[pat_id] = {
                    "patient_id": pat_id,
                    "score": round(score, 4),
                    "record_date": meta.get("record_date") or None,
                    "resource_type": meta.get("resource_type", "Unknown"),
                    "chunk_snippet": meta.get("summary_snippet", ""),
                }

        # 5. Enrich with patient names, MRNs, and sentence-aware AI summary snippets
        conn = sqlite3.connect(self._db_path)
        try:
            patients_map, summaries_map = self._get_patient_metadata_cache(conn)
        finally:
            conn.close()

        # Sort grouped patients by relevance score descending
        sorted_patients = sorted(
            patient_best_matches.values(),
            key=lambda x: x["score"],
            reverse=True,
        )[: request.limit]

        results = []
        for match in sorted_patients:
            pat_id = match["patient_id"]
            p_info = patients_map.get(pat_id, {"name": pat_id, "mrn": "Unknown"})
            # Extract sentence-aware snippet from cached summary (or chunk fallback)
            raw_summary = summaries_map.get(pat_id) or match["chunk_snippet"]
            summary_snippet = extract_sentence_aware_snippet(raw_summary, min_chars=150)

            results.append(
                PatientSearchResult(
                    patient_id=pat_id,
                    patient_name=p_info["name"],
                    mrn=p_info["mrn"],
                    record_date=match["record_date"],
                    resource_type=match["resource_type"],
                    score=match["score"],
                    summary_snippet=summary_snippet,
                )
            )

        return SearchResponse(
            query=request.query,
            total_patients=len(results),
            results=results,
        )

    def get_patient_detail(self, patient_id: str) -> PatientDetailResponse:
        """Retrieves patient demographics, full clinical summary, and linked FHIR resources."""
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.cursor()

            # 1. Demographics
            cursor.execute("""
                SELECT patient_id, first_name, last_name, gender, date_of_birth, mrn, facility_id
                FROM patients
                WHERE patient_id = ?
            """, (patient_id,))
            p_row = cursor.fetchone()
            if not p_row:
                raise PatientNotFoundError(f"Patient with ID '{patient_id}' not found.")

            demographics = PatientProfileDemographics(
                patient_id=p_row[0],
                first_name=p_row[1],
                last_name=p_row[2],
                gender=p_row[3],
                date_of_birth=p_row[4],
                mrn=p_row[5],
                facility_id=p_row[6],
            )

            # 2. Latest AI Summary
            cursor.execute("""
                SELECT summary_json, narrative_summary, chief_concern, word_count, confidence_score
                FROM clinical_summaries
                WHERE patient_id = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (patient_id,))
            s_row = cursor.fetchone()
            summary_data = None
            if s_row and s_row[0]:
                import json
                try:
                    summary_data = json.loads(s_row[0])
                except Exception:
                    summary_data = None

            if summary_data is not None and s_row:
                if "narrative_summary" not in summary_data and s_row[1]:
                    summary_data["narrative_summary"] = s_row[1]
                if "chief_concern" not in summary_data and s_row[2]:
                    summary_data["chief_concern"] = s_row[2]

            # 3. Linked FHIR Resources with consistent encounter IDs
            # Build URN -> encounter_id mapping from fhir_bundles
            cursor.execute("SELECT bundle_json FROM fhir_bundles WHERE patient_id = ?", (patient_id,))
            b_row = cursor.fetchone()
            urn_to_enc_id = {}
            if b_row and b_row[0]:
                import json
                try:
                    b_obj = json.loads(b_row[0])
                    for entry in b_obj.get("entry", []):
                        full_url = entry.get("fullUrl")
                        res = entry.get("resource", {})
                        if res.get("resourceType") == "Encounter":
                            enc_res_id = res.get("id")
                            if full_url and enc_res_id:
                                urn_to_enc_id[full_url] = enc_res_id
                except Exception:
                    pass

            cursor.execute("""
                SELECT resource_id, resource_type, effective_date, encounter_id, fhir_json
                FROM fhir_resources
                WHERE patient_id = ?
                ORDER BY effective_date DESC
            """, (patient_id,))
            linked = []
            for r_row in cursor.fetchall():
                import json
                try:
                    r_json = json.loads(r_row[4])
                except Exception:
                    r_json = {}

                raw_enc_id = r_row[3]
                # Resolve URN references so encounter_id is strictly consistent
                consistent_enc_id = urn_to_enc_id.get(raw_enc_id, raw_enc_id)

                linked.append(
                    LinkedFHIRResource(
                        resource_id=r_row[0],
                        resource_type=r_row[1],
                        effective_date=r_row[2],
                        encounter_id=consistent_enc_id,
                        fhir_json=r_json,
                    )
                )

            return PatientDetailResponse(
                patient=demographics,
                summary=summary_data,
                linked_resources=linked,
            )
        finally:
            conn.close()
