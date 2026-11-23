#!/usr/bin/env python3
"""CLI runner to build or update the clinical vector index in Chroma.

Usage:
    python backend/scripts/build_vector_index.py [--backend local|mock|huggingface] [--rebuild]
"""
import argparse
import logging
import sys
from pathlib import Path

# Add backend directory to sys.path
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings
from app.search.embeddings import get_embedding_client
from app.search.indexer import ClinicalIndexer
from app.search.vector_store import VectorStoreManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("build_vector_index")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build persistent clinical vector index.")
    parser.add_argument(
        "--backend",
        type=str,
        default=None,
        choices=["local", "mock", "huggingface"],
        help="Override embedding backend (default from config/backend/.env)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Clear existing vector collection and rebuild from scratch",
    )
    args = parser.parse_args()

    settings = get_settings()
    backend = args.backend or settings.embedding_backend
    print(f"=== Initializing Clinical Vector Indexer (Backend: {backend}) ===")

    embedder = get_embedding_client(settings=settings, backend_override=backend)
    vector_store = VectorStoreManager(settings=settings)
    indexer = ClinicalIndexer(vector_store=vector_store, embedding_client=embedder, settings=settings)

    results = indexer.build_index(force_rebuild=args.rebuild)
    print("\n=== Vector Index Build Summary ===")
    print(f"Total Vector Count: {results['total_indexed']}")
    print(f"FHIR Documents:     {results['fhir_count']}")
    print(f"AI Summaries:       {results['summary_count']}")
    print(f"Upserted Records:   {results['upserted']}")
    print(f"Skipped (Unchanged):{results['skipped']}")
    print(f"Stale Removed:      {results['stale_removed']}")
    print(f"Active Backend:     {results['backend']}")
    print(f"Active Model:       {results['model']}")
    print("===================================\n")


if __name__ == "__main__":
    main()
