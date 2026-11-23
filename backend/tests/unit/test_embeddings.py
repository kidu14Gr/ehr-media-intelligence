"""Unit tests for embedding clients (Mock, Hugging Face, and Local singleton)."""
import math
from unittest.mock import MagicMock, patch

import pytest

from app.core.exceptions import LLMClientError
from app.search.embeddings import (
    BaseEmbeddingClient,
    HuggingFaceEmbeddingClient,
    LocalEmbeddingClient,
    MockEmbeddingClient,
    get_embedding_client,
)


def test_mock_embedding_deterministic():
    """Validates that MockEmbeddingClient produces deterministic, normalized 384-dim vectors."""
    client = MockEmbeddingClient()
    assert client.dimension == 384
    assert client.backend_name == "mock"

    t1 = "Patient presents with severe hypertension."
    t2 = "Routine lab screening."

    res1 = client.embed_texts([t1, t2])
    assert len(res1) == 2
    assert len(res1[0]) == 384
    assert len(res1[1]) == 384

    # Check L2 normalization
    norm0 = math.sqrt(sum(x * x for x in res1[0]))
    assert abs(norm0 - 1.0) < 1e-4

    # Check determinism
    res2 = client.embed_texts([t1])
    assert res1[0] == res2[0]


def test_mock_embedding_query():
    """Validates single query embedding matches multi-text embedding."""
    client = MockEmbeddingClient()
    q = "chest pain and wheezing"
    vec_query = client.embed_query(q)
    vec_batch = client.embed_texts([q])[0]
    assert len(vec_query) == 384
    assert vec_query == vec_batch


def test_hf_client_missing_key_raises():
    """Validates that Hugging Face client raises LLMClientError when no API key is provided."""
    client = HuggingFaceEmbeddingClient(api_key=None)
    with pytest.raises(LLMClientError, match="HUGGINGFACE_API_KEY is not configured"):
        client.embed_texts(["test clinical text"])


def test_hf_client_retry_on_503_and_success():
    """Validates Hugging Face client handles HTTP 503 warming with retry."""
    client = HuggingFaceEmbeddingClient(api_key="hf_test_dummy_key", max_retries=2)
    fake_vector = [0.1] * 384

    mock_resp_503 = MagicMock()
    mock_resp_503.status_code = 503
    mock_resp_503.text = "Model loading"

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = [fake_vector]

    with patch("httpx.Client.post", side_effect=[mock_resp_503, mock_resp_200]), \
         patch("time.sleep", return_value=None):
        results = client.embed_texts(["fever and chills"])
        assert len(results) == 1
        assert len(results[0]) == 384
        assert results[0] == fake_vector
