"""Persistent Chroma vector store manager with backend compatibility verification.

Ensures that the active embedding backend and model strictly match the backend that
built the index, preventing semantic space contamination across different embedders.
"""
import json
import logging
from pathlib import Path
from typing import Any, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.config import Settings, get_settings
from app.core.exceptions import IndexBackendMismatchError

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "index_manifest.json"
COLLECTION_NAME = "clinical_records"


class VectorStoreManager:
    """Manages persistent Chroma vector storage, manifest metadata, and querying."""

    def __init__(
        self,
        persist_dir: Optional[Path] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._persist_dir = persist_dir or self._settings.chroma_persist_dir
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._persist_dir / MANIFEST_FILENAME

        # Initialize Chroma persistent client
        self._client = chromadb.PersistentClient(
            path=str(self._persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        # Cosine distance collection: distance in [0, 2], similarity = 1 - distance
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def persist_dir(self) -> Path:
        return self._persist_dir

    @property
    def collection(self) -> Any:
        return self._collection

    def get_manifest(self) -> Optional[dict[str, Any]]:
        """Reads persisted index manifest if it exists."""
        if not self._manifest_path.exists():
            return None
        try:
            with open(self._manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Could not read index manifest: %s", e)
            return None

    def save_manifest(
        self,
        backend_name: str,
        model_name: str,
        dimension: int,
        document_count: int,
    ) -> None:
        """Persists metadata manifest recording which embedder constructed the index."""
        manifest = {
            "embedding_backend": backend_name,
            "model_name": model_name,
            "dimension": dimension,
            "document_count": document_count,
            "collection_name": COLLECTION_NAME,
        }
        with open(self._manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        logger.info("Saved index manifest: %s", manifest)

    def verify_backend_compatibility(self, expected_backend: str, expected_model: str) -> None:
        """Enforces that configured embedder matches the backend that created the index.

        Raises IndexBackendMismatchError if the active configuration differs.
        """
        manifest = self.get_manifest()
        if manifest is None:
            # No index has been built yet
            return

        idx_backend = manifest.get("embedding_backend")
        idx_model = manifest.get("model_name")

        if idx_backend != expected_backend or idx_model != expected_model:
            raise IndexBackendMismatchError(
                f"Vector index was built with backend '{idx_backend}' ({idx_model}), "
                f"but current configuration is '{expected_backend}' ({expected_model}). "
                "Please rebuild the vector index to prevent mixing incompatible embeddings."
            )

    def upsert_records(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
        documents: list[str],
    ) -> int:
        """Idempotently upserts clinical documents and embeddings into Chroma."""
        if not ids:
            return 0

        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents,
        )
        return len(ids)

    def remove_stale_records(self, active_ids: set[str]) -> int:
        """Deletes vector entries that no longer exist in the primary database."""
        existing = self._collection.get(include=[])
        existing_ids = set(existing.get("ids", []))
        stale_ids = existing_ids - active_ids
        if stale_ids:
            logger.info("Removing %d stale vector records from Chroma", len(stale_ids))
            self._collection.delete(ids=list(stale_ids))
            return len(stale_ids)
        return 0

    def query_vectors(
        self,
        query_embedding: list[float],
        n_results: int = 50,
        where: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Queries Chroma collection with precomputed query embedding vector."""
        count = self._collection.count()
        if count == 0:
            return {"ids": [[]], "distances": [[]], "metadatas": [[]], "documents": [[]]}

        actual_n = min(n_results, count)
        kwargs: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": actual_n,
            "include": ["metadatas", "documents", "distances"],
        }
        if where:
            kwargs["where"] = where

        return self._collection.query(**kwargs)

    def count(self) -> int:
        """Returns total active vector count in collection."""
        return self._collection.count()

    def reset_collection(self) -> None:
        """Deletes and recreates the collection (used for test teardown / full rebuild)."""
        self._client.delete_collection(name=COLLECTION_NAME)
        self._collection = self._client.create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        if self._manifest_path.exists():
            self._manifest_path.unlink()
