"""Pydantic Settings — all configuration from environment variables."""

from __future__ import annotations

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings — loaded entirely from env vars."""

    # ── Required ────────────────────────────────────────────────────────
    aes_secret: str

    # ── workerd tunables ────────────────────────────────────────────────
    workerd_timeout_ms: int = 30_000
    workerd_max_memory_mb: int = 128

    # ── Common tunables ─────────────────────────────────────────────────
    rate_limit_rps: float = 10.0
    max_response_bytes: int = 5_242_880  # 5 MB
    schema_cache_ttl_s: int = 300  # 5 min
    log_level: str = "INFO"
    debug: bool = False

    # ── Server ──────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    base_url: str = ""  # e.g. https://mcp.example.com

    # ── Derived (populated by model_validator) ──────────────────────────
    effective_base_url: str = ""

    model_config = {"env_prefix": "", "case_sensitive": False}

    @field_validator("aes_secret")
    @classmethod
    def _validate_aes_secret(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("AES_SECRET must be at least 32 characters")
        return v

    @model_validator(mode="after")
    def _compute_effective_base_url(self) -> "Settings":
        if self.base_url:
            self.effective_base_url = self.base_url.rstrip("/")
        else:
            self.effective_base_url = f"http://localhost:{self.port}"
        return self
