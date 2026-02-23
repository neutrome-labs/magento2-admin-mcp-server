"""Pydantic Settings — all configuration from environment variables."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings


class FeaturedAPI:
    """Parsed representation of a single FEATURED_APIS_<n> entry."""

    __slots__ = ("priority", "name", "method", "path", "description", "params")

    def __init__(
        self,
        priority: int,
        name: str,
        method: str,
        path: str,
        description: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.priority = priority
        self.name = name
        self.method = method.upper()
        self.path = path
        self.description = description
        self.params = params


class Settings(BaseSettings):
    """Application settings — loaded entirely from env vars."""

    # ── Required ────────────────────────────────────────────────────────
    aes_secret: str

    # ── Mode switch ─────────────────────────────────────────────────────
    featured_apis_all: bool = False

    # ── workerd tunables (Mode 2) ───────────────────────────────────────
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
    base_url: str = ""  # e.g. https://mcp.example.com — defaults to http://localhost:{port}

    # ── Derived (populated by model_validator) ──────────────────────────
    effective_base_url: str = ""
    featured_apis: list[FeaturedAPI] = []

    model_config = {"env_prefix": "", "case_sensitive": False}

    @field_validator("aes_secret")
    @classmethod
    def _validate_aes_secret(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("AES_SECRET must be at least 32 characters")
        return v

    @model_validator(mode="after")
    def _parse_featured_apis(self) -> "Settings":
        """
        Scan environment for FEATURED_APIS_<n> entries and build the list.

        Expected format:
            FEATURED_APIS_<priority>=<name>::<method>::<path>
            FEATURED_APIS_<priority>_DESCRIPTION=<text>
            FEATURED_APIS_<priority>_PARAMS=<json>           # optional
        """
        # Compute effective base URL for OAuth issuer
        if self.base_url:
            self.effective_base_url = self.base_url.rstrip("/")
        else:
            self.effective_base_url = f"http://localhost:{self.port}"

        if self.featured_apis_all:
            return self  # Mode 2 — skip scanning

        pattern = re.compile(r"^FEATURED_APIS_(\d+)$", re.IGNORECASE)
        apis: list[FeaturedAPI] = []

        for key, value in os.environ.items():
            m = pattern.match(key)
            if not m:
                continue
            priority = int(m.group(1))
            parts = value.split("::")
            if len(parts) != 3:
                raise ValueError(
                    f"{key}={value!r} must follow <name>::<method>::<path> format"
                )
            name, method, path = parts

            desc_key = f"{key}_DESCRIPTION"
            description = os.environ.get(desc_key, f"{name} tool")

            params_key = f"{key}_PARAMS"
            params_raw = os.environ.get(params_key)
            params = json.loads(params_raw) if params_raw else None

            apis.append(
                FeaturedAPI(
                    priority=priority,
                    name=name,
                    method=method,
                    path=path,
                    description=description,
                    params=params,
                )
            )

        apis.sort(key=lambda a: a.priority)
        self.featured_apis = apis
        return self

    @property
    def has_featured_apis(self) -> bool:
        return len(self.featured_apis) > 0
