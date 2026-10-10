from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

RAG_DIR = Path(__file__).resolve().parent
REPO_ROOT = RAG_DIR.parent


class RagSettings(BaseSettings):
    """RAG settings from RAG_* environment variables (DOCUMENT_PATH is also accepted)."""

    model_config = SettingsConfigDict(env_prefix="RAG_", env_file=".env", extra="ignore", populate_by_name=True)

    document_path: Path = Field(
        RAG_DIR / "documents", validation_alias=AliasChoices("DOCUMENT_PATH", "RAG_DOCUMENT_PATH")
    )
    index_dir: Path = RAG_DIR / "index"
    model_cache_dir: Path = REPO_ROOT / ".cache" / "models"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    chunk_max_words: int = Field(160, ge=40, le=1000)
    chunk_overlap_words: int = Field(30, ge=0, le=200)
