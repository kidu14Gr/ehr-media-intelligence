"""FastAPI application entrypoint for EHR Media Intelligence Platform."""
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.patients import router as patients_router
from app.api.routes.search import router as search_router
from app.core.config import get_settings
from app.search.embeddings import get_embedding_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle manager.

    Pre-loads the configured embedding model into memory once at startup
    so that subsequent request cycles encounter zero initialization latency.
    """
    settings = get_settings()
    logger.info(
        "Starting EHR Media Intelligence API (Embedding Backend: %s, Model: %s)",
        settings.embedding_backend,
        settings.embedding_model_name,
    )
    # Preload singleton model once at application startup
    try:
        embedder = get_embedding_client(settings=settings)
        logger.info(
            "Embedding model '%s' successfully loaded into application memory.",
            embedder.model_name,
        )
    except Exception as e:
        logger.error("Failed to preload embedding model at startup: %s", e)

    yield

    logger.info("Shutting down EHR Media Intelligence API.")


def create_app() -> FastAPI:
    """Factory creating configured FastAPI application instance."""
    settings = get_settings()

    app = FastAPI(
        title="EHR Media Intelligence Platform API",
        description="FastAPI service for clinical semantic search, patient detail review, and LLM summarization.",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS Middleware configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routers
    app.include_router(search_router)
    app.include_router(patients_router)

    @app.get("/health", tags=["system"], summary="Health check endpoint")
    def health_check() -> dict[str, str]:
        return {"status": "ok", "version": "1.0.0"}

    return app


app = create_app()
