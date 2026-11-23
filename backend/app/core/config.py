"""Application settings and configuration management via Pydantic Settings.

Ensures that model names, token limits, timeouts, and cache paths are strictly
configurable from the environment and backend/.env with no hardcoded values.
"""
from pathlib import Path
from typing import Any, Literal, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    """Platform configuration loaded from environment variables and backend/.env."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM Provider Selection
    llm_provider: Literal["groq", "mock", "anthropic", "openai"] = Field(
        default="groq",
        alias="LLM_PROVIDER",
        description="Active LLM provider: 'groq', 'mock', 'anthropic', or 'openai'",
    )

    # Groq Configuration
    groq_api_key: Optional[str] = Field(
        default=None,
        alias="GROQ_API_KEY",
        description="API key for Groq Cloud",
    )
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        alias="GROQ_MODEL",
        description="Groq model identifier",
    )

    # LLM Inference Parameters
    llm_temperature: float = Field(
        default=0.1,
        alias="LLM_TEMPERATURE",
        description="Inference temperature (low for clinical factual accuracy)",
    )
    llm_max_tokens: int = Field(
        default=1000,
        alias="LLM_MAX_TOKENS",
        description="Maximum output tokens for generated summary",
    )
    llm_timeout_seconds: float = Field(
        default=30.0,
        alias="LLM_TIMEOUT_SECONDS",
        description="Timeout in seconds for LLM API requests",
    )
    llm_max_retries: int = Field(
        default=3,
        alias="LLM_MAX_RETRIES",
        description="Maximum retry attempts on rate-limits or timeouts",
    )

    # Prompt & Word Count Controls
    prompt_version: str = Field(
        default="v1.0",
        alias="PROMPT_VERSION",
        description="Prompt template version tag (included in cache key)",
    )
    summary_word_target: int = Field(
        default=150,
        alias="SUMMARY_WORD_TARGET",
        description="Target word count instructed in prompts (~150 words)",
    )
    summary_word_limit: int = Field(
        default=200,
        alias="SUMMARY_WORD_LIMIT",
        description="Strict maximum word limit for clinical narrative (200 words)",
    )

    # Embedding & Vector Search Settings
    embedding_backend: Literal["local", "huggingface", "mock"] = Field(
        default="local",
        alias="EMBEDDING_BACKEND",
        description="Active embedding backend: 'local' (SentenceTransformers), 'huggingface' (hosted API), or 'mock'",
    )
    embedding_model_name: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        alias="EMBEDDING_MODEL_NAME",
        description="SentenceTransformers model identifier for local inference",
    )
    huggingface_api_key: Optional[str] = Field(
        default=None,
        alias="HUGGINGFACE_API_KEY",
        description="API key for Hugging Face Hosted Inference API",
    )
    hf_embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        alias="HF_EMBEDDING_MODEL",
        description="Model identifier for Hugging Face Hosted Inference API",
    )
    chroma_persist_dir: Path = Field(
        default=PROJECT_ROOT / "storage" / "chroma",
        alias="CHROMA_PERSIST_DIR",
        description="Directory path for persistent Chroma vector store",
    )
    cors_origins: list[str] = Field(
        default=[
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ],
        alias="CORS_ORIGINS",
        description="Permitted CORS origins for frontend client",
    )
    min_relevance_score: float = Field(
        default=0.15,
        alias="MIN_RELEVANCE_SCORE",
        description="Minimum relevance score threshold for semantic search results",
    )

    # Cache & Storage Paths
    summary_cache_enabled: bool = Field(
        default=True,
        alias="SUMMARY_CACHE_ENABLED",
        description="Whether clinical summary caching is active",
    )
    summary_cache_dir: Path = Field(
        default=PROJECT_ROOT / "storage" / "cache" / "summaries",
        alias="SUMMARY_CACHE_DIR",
        description="Path to JSON summary cache directory",
    )
    database_path: Path = Field(
        default=PROJECT_ROOT / "storage" / "clinical_platform.db",
        alias="DATABASE_PATH",
        description="Path to SQLite platform database",
    )

    def model_post_init(self, __context: Any) -> None:
        """Ensures relative filesystem paths resolve against project root."""
        if not self.database_path.is_absolute():
            self.database_path = PROJECT_ROOT / self.database_path
        if not self.summary_cache_dir.is_absolute():
            self.summary_cache_dir = PROJECT_ROOT / self.summary_cache_dir
        if not self.chroma_persist_dir.is_absolute():
            self.chroma_persist_dir = PROJECT_ROOT / self.chroma_persist_dir

    def get_active_model_name(self) -> str:
        """Returns the active model name based on provider configuration."""
        if self.llm_provider == "groq":
            return self.groq_model
        return f"mock-{self.groq_model}"


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Returns singleton application settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Resets cached settings singleton (useful for test overrides)."""
    global _settings
    _settings = None
