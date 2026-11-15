"""
Application configuration via environment variables.

Uses Pydantic Settings so every value can be overridden with an env var
or a ``.env`` file.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> str:
    """Resolve the default data directory (relative to project root)."""
    return str(Path(__file__).resolve().parent.parent.parent / "data")


class Settings(BaseSettings):
    """Central configuration — all values are overridable via env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Paths ────────────────────────────────────────────────────────────
    DATA_DIR: str = _default_data_dir()

    @property
    def upload_dir(self) -> Path:
        p = Path(self.DATA_DIR) / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def chroma_dir(self) -> Path:
        p = Path(self.DATA_DIR) / "chroma_db"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def metadata_db_path(self) -> Path:
        Path(self.DATA_DIR).mkdir(parents=True, exist_ok=True)
        return Path(self.DATA_DIR) / "metadata.db"

    # ── Upload limits ────────────────────────────────────────────────────
    MAX_FILE_SIZE_MB: int = 50
    ALLOWED_EXTENSIONS: list[str] = [".pdf"]

    # ── Chunking ─────────────────────────────────────────────────────────
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 200

    # ── Embedding ────────────────────────────────────────────────────────
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_BATCH_SIZE: int = 64
    EMBEDDING_DIMENSION: int = 384

    # ── OCR ──────────────────────────────────────────────────────────────
    OCR_ENABLED: bool = True
    OCR_LANGUAGE: str = "eng"
    OCR_MIN_TEXT_LENGTH: int = 50  # pages with fewer chars trigger OCR

    # ── Server ───────────────────────────────────────────────────────────
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    CORS_ORIGINS: list[str] = ["*"]
    LOG_LEVEL: str = "INFO"

    # ── App metadata ─────────────────────────────────────────────────────
    APP_NAME: str = "PDF RAG System"
    APP_VERSION: str = "0.1.0"

    # ── LLM / RAG ────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_API_BASE: str | None = None
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_TIMEOUT_SECONDS: float = 30.0  # SRS: AI WAIT LIMIT
    TOP_K_RETRIEVAL: int = 5
    # Keep every top-k chunk (cosine distance is 0..2) so the ECA module sees all scores.
    SIMILARITY_THRESHOLD: float = 2.0  # Ignore chunks with distance above this

    # ── Dual-State tutoring (SRS 3.2.5 and 3.2.6) ────────────────────────
    PARTIAL_LIMIT: float = 0.50  # best score below this -> Insufficient
    STRONG_LIMIT: float = 0.75  # best score for Strong (with 2+ passages above PARTIAL_LIMIT)
    DECISION_TABLE_PATH: str = str(Path(__file__).resolve().parent / "data" / "decision_table.json")

    # ── Accounts (SRS 3.2.1, UAM) ────────────────────────────────────────
    PASSWORD_MIN_LENGTH: int = 8
    TOKEN_IDLE_MINUTES: int = 30  # login token stops working after this much inactivity
    MAX_FAILED_LOGINS: int = 5  # wrong passwords allowed within the window below
    FAILED_LOGIN_WINDOW_MINUTES: int = 10
    LOCK_MINUTES: int = 5
    # The administrator account is created at install time from these (set them in .env).
    ADMIN_NAME: str = "Administrator"
    ADMIN_EMAIL: str = ""
    ADMIN_PASSWORD: str = ""

# Singleton — import this everywhere instead of constructing a new instance.
settings = Settings()
