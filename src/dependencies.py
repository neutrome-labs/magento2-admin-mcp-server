"""Dependency injection factories for Magento session, client, and schema.

These ``Depends()``-compatible callables are used by tool functions to get
pre-authenticated Magento resources directly from the JWT access token.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastmcp.dependencies import CurrentAccessToken, Depends
from fastmcp.server.auth import AccessToken

from src.auth.tokens import TokenEngine
from src.magento.client import MagentoSession, create_magento_client, fetch_and_cache_schema
from src.settings import Settings

# ── Singleton settings and engine (created once at import time) ─────────
# We use a lazy-init pattern to avoid import-time env reading.
_settings: Settings | None = None
_engine: TokenEngine | None = None


def _get_engine() -> TokenEngine:
    global _settings, _engine
    if _engine is None:
        _settings = Settings()  # type: ignore[call-arg]
        _engine = TokenEngine(_settings.aes_secret)
    return _engine


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings


def init_dependencies(settings: Settings) -> None:
    """Initialise the dependency singletons with pre-built settings.

    Called once from ``server.py`` at startup so we don't re-read env vars.
    """
    global _settings, _engine
    _settings = settings
    _engine = TokenEngine(settings.aes_secret)


# ── Dependency: MagentoSession ──────────────────────────────────────────

async def get_magento_session(
    token: AccessToken = CurrentAccessToken(),
) -> MagentoSession:
    """Decrypt the Magento URL and admin token from the JWT claims."""
    engine = _get_engine()
    claims = token.claims
    magento_url = engine.decrypt(claims["url"])
    magento_key = engine.decrypt(claims["key"])
    return MagentoSession(url=magento_url, token=magento_key)


# ── Dependency: pre-authed httpx.AsyncClient ────────────────────────────

async def get_magento_client(
    session: MagentoSession = Depends(get_magento_session),
) -> httpx.AsyncClient:
    """Return a pre-authenticated Magento REST client."""
    return create_magento_client(session)


# ── Dependency: OpenAPI schema (cached per session) ─────────────────────

async def get_openapi_schema(
    session: MagentoSession = Depends(get_magento_session),
) -> dict[str, Any]:
    """Fetch (and cache) the Magento OpenAPI schema for the current session."""
    settings = _get_settings()
    return await fetch_and_cache_schema(session, ttl=settings.schema_cache_ttl_s)
