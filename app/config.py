"""Typed application settings, loaded from environment / .env once at startup."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- OpenAI ---------------------------------------------------------------
    openai_api_key: str
    vector_store_id: str = "vs_your_vector_store_id"
    model: str = "gpt-4.1"
    max_num_results: int = 10

    # --- Source documents (the PDFs backing the vector store) -----------------
    # Folder holding the original PDFs. Filenames must match what was uploaded
    # to the vector store so citations can be mapped back to a file. Relative
    # paths are resolved against the project root.
    sources_dir: str = "sources"

    # --- Database -------------------------------------------------------------
    database_url: str
    db_pool_min: int = 1
    db_pool_max: int = 10

    # --- HTTP / CORS ----------------------------------------------------------
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    environment: str = "production"
    log_level: str = "INFO"

    # --- App metadata ---------------------------------------------------------
    app_name: str = "EzJudgements API"
    app_version: str = "1.0.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
