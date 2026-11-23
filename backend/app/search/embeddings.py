"""Embedding client abstractions for clinical text vectorization.

Provides unified interface for local SentenceTransformers inference (default),
deterministic Mock client for fast offline testing, and Hugging Face Hosted Inference
API client for remote deployment.
"""
import abc
import hashlib
import logging
import math
import time
from typing import Optional

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import LLMClientError

logger = logging.getLogger(__name__)


class BaseEmbeddingClient(abc.ABC):
    """Abstract base interface for clinical text embeddings."""

    @property
    @abc.abstractmethod
    def backend_name(self) -> str:
        """Name of the embedding backend ('local', 'huggingface', 'mock')."""
        pass

    @property
    @abc.abstractmethod
    def model_name(self) -> str:
        """Identifier of the underlying embedding model."""
        pass

    @property
    @abc.abstractmethod
    def dimension(self) -> int:
        """Vector dimensionality of produced embeddings (typically 384)."""
        pass

    @abc.abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generates embedding vectors for a batch of input text passages."""
        pass

    @abc.abstractmethod
    def embed_query(self, query: str) -> list[float]:
        """Generates an embedding vector for a single search query."""
        pass


class LocalEmbeddingClient(BaseEmbeddingClient):
    """Local embedding client using cached sentence-transformers.

    Loads the model once into memory at initialization and reuses the instance
    across all requests without repeated initialization overhead.
    """

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self._dimension = 384
        logger.info("Initializing LocalEmbeddingClient with model %s", model_name)
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(model_name)
        except Exception as e:
            logger.error("Failed to load local SentenceTransformer: %s", e)
            raise RuntimeError(
                f"Could not load local embedding model '{model_name}': {e}"
            ) from e

    @property
    def backend_name(self) -> str:
        return "local"

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings = self._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.tolist()

    def embed_query(self, query: str) -> list[float]:
        embedding = self._model.encode(
            [query],
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embedding[0].tolist()


class MockEmbeddingClient(BaseEmbeddingClient):
    """Fast, deterministic pseudo-random embedding generator for offline testing.

    Generates normalized 384-dimensional unit vectors derived from SHA-256 hash
    of input text. Zero external dependencies, zero network requests, <0.1ms per call.
    """

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self._dimension = 384

    @property
    def backend_name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return f"mock-{self._model_name}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def _hash_to_vector(self, text: str) -> list[float]:
        # Generate 384 pseudo-random float values using seeded MD5/SHA blocks
        vec = []
        base_hash = hashlib.sha256(text.encode("utf-8")).digest()
        for i in range(self._dimension):
            # Deterministic variation per dimension
            byte_val = base_hash[i % 32]
            val = math.sin((i + 1) * (byte_val + 1))
            vec.append(val)

        # L2 Normalize
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            return [x / norm for x in vec]
        return [0.0] * self._dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_to_vector(t) for t in texts]

    def embed_query(self, query: str) -> list[float]:
        return self._hash_to_vector(query)


class HuggingFaceEmbeddingClient(BaseEmbeddingClient):
    """Hugging Face Hosted Inference API embedding client for remote deployment.

    Targets the hosted Feature Extraction endpoint with backoff retry on HTTP 503
    (model loading / warming up) and HTTP 429.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._dimension = 384
        self._endpoint_url = (
            f"https://api-inference.huggingface.co/pipeline/feature-extraction/{model_name}"
        )

    @property
    def backend_name(self) -> str:
        return "huggingface"

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def _request_embeddings(self, inputs: list[str]) -> list[list[float]]:
        if not self._api_key:
            raise LLMClientError(
                "HUGGINGFACE_API_KEY is not configured in environment or .env."
            )

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "inputs": inputs,
            "options": {"wait_for_model": True},
        }

        with httpx.Client(timeout=self._timeout) as client:
            for attempt in range(1, self._max_retries + 1):
                try:
                    resp = client.post(self._endpoint_url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        raw_data = resp.json()
                        # If single input, API might return 2D list or 1D list
                        if isinstance(raw_data, list):
                            if len(raw_data) > 0 and isinstance(raw_data[0], list):
                                # Check if 3D token embeddings or 2D pooled sentence embeddings
                                if isinstance(raw_data[0][0], list):
                                    # Mean pooling fallback
                                    pooled = []
                                    for sentence_tokens in raw_data:
                                        num_tokens = len(sentence_tokens)
                                        dim = len(sentence_tokens[0])
                                        mean_vec = [
                                            sum(sentence_tokens[t][d] for t in range(num_tokens)) / num_tokens
                                            for d in range(dim)
                                        ]
                                        norm = math.sqrt(sum(x * x for x in mean_vec))
                                        pooled.append([x / norm for x in mean_vec] if norm > 0 else mean_vec)
                                    return pooled
                                return raw_data
                            elif len(raw_data) > 0 and isinstance(raw_data[0], (int, float)):
                                return [raw_data]
                        raise LLMClientError(f"Unexpected Hugging Face embedding format: {raw_data}")

                    if resp.status_code in (503, 429) and attempt < self._max_retries:
                        backoff = 2.0 ** attempt
                        logger.warning(
                            "Hugging Face API returned HTTP %s (attempt %s/%s). Retrying in %.1fs...",
                            resp.status_code,
                            attempt,
                            self._max_retries,
                            backoff,
                        )
                        time.sleep(backoff)
                        continue

                    raise LLMClientError(
                        f"Hugging Face API returned status {resp.status_code}: {resp.text}"
                    )
                except (httpx.TimeoutException, httpx.NetworkError) as net_err:
                    if attempt < self._max_retries:
                        backoff = 2.0 ** attempt
                        time.sleep(backoff)
                        continue
                    raise LLMClientError(f"Hugging Face network failure: {net_err}") from net_err

        raise LLMClientError("Exceeded maximum retries for Hugging Face inference.")

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._request_embeddings(texts)

    def embed_query(self, query: str) -> list[float]:
        results = self._request_embeddings([query])
        return results[0]


# Singleton local client storage so model is loaded once
_local_embedding_singleton: Optional[LocalEmbeddingClient] = None


def get_embedding_client(
    settings: Optional[Settings] = None,
    backend_override: Optional[str] = None,
) -> BaseEmbeddingClient:
    """Factory creating or returning singleton embedding client based on configuration."""
    global _local_embedding_singleton
    active_settings = settings or get_settings()
    backend = backend_override or active_settings.embedding_backend

    if backend == "mock":
        return MockEmbeddingClient(model_name=active_settings.embedding_model_name)
    elif backend == "huggingface":
        return HuggingFaceEmbeddingClient(
            api_key=active_settings.huggingface_api_key,
            model_name=active_settings.hf_embedding_model,
        )
    else:
        # Default: local sentence-transformers loaded ONCE
        if _local_embedding_singleton is None:
            _local_embedding_singleton = LocalEmbeddingClient(
                model_name=active_settings.embedding_model_name
            )
        return _local_embedding_singleton
