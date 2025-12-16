from __future__ import annotations

from pydantic import BaseModel, Field


class Settings(BaseModel):
    database_url: str = Field(default="postgresql://postgres:postgres@localhost:5432/polymarket")
    gamma_base_url: str = Field(default="https://gamma-api.polymarket.com")
    clob_host: str = Field(default="https://clob.polymarket.com")
    chain_id: int = Field(default=137)
    log_level: str = Field(default="INFO")


def load_settings() -> Settings:
    """
    Load settings from environment variables (optionally via python-dotenv in callers).
    """
    import os

    return Settings(
        database_url=os.getenv("DATABASE_URL", Settings.model_fields["database_url"].default),
        gamma_base_url=os.getenv("GAMMA_BASE_URL", Settings.model_fields["gamma_base_url"].default),
        clob_host=os.getenv("CLOB_HOST", Settings.model_fields["clob_host"].default),
        chain_id=int(os.getenv("CHAIN_ID", str(Settings.model_fields["chain_id"].default))),
        log_level=os.getenv("LOG_LEVEL", Settings.model_fields["log_level"].default),
    )


