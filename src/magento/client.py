"""Magento REST + OpenAPI client utilities."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MagentoSession:
    """Decrypted Magento credentials derived from the JWT access_token."""

    url: str
    token: str

    @property
    def identity_hash(self) -> str:
        """A non-sensitive hash usable as a cache key."""
        return hashlib.sha256(self.url.encode()).hexdigest()[:16]


# ── Schema cache ────────────────────────────────────────────────────────

@dataclass
class _CacheEntry:
    schema: dict[str, Any]
    expires_at: float


_schema_cache: dict[str, _CacheEntry] = {}


async def fetch_and_cache_schema(
    session: MagentoSession,
    ttl: int = 300,
) -> dict[str, Any]:
    """Fetch the Magento OpenAPI schema, caching per session identity hash.

    Args:
        session: The decrypted Magento session.
        ttl: Cache time-to-live in seconds (default 300 = 5 min).

    Returns:
        Parsed OpenAPI schema as a dict.
    """
    cache_key = session.identity_hash
    now = time.time()

    entry = _schema_cache.get(cache_key)
    if entry and entry.expires_at > now:
        return entry.schema

    schema_url = f"{session.url}/rest/all/schema?services=all"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            schema_url,
            headers={"Authorization": f"Bearer {session.token}"},
        )
        resp.raise_for_status()
        schema = resp.json()

    _schema_cache[cache_key] = _CacheEntry(schema=schema, expires_at=now + ttl)
    logger.info(
        "Fetched OpenAPI schema for %s (%d paths, cached %ds)",
        cache_key,
        len(schema.get("paths", {})),
        ttl,
    )
    return schema


def create_magento_client(session: MagentoSession) -> httpx.AsyncClient:
    """Create a pre-authenticated ``httpx.AsyncClient`` for Magento REST calls."""
    return httpx.AsyncClient(
        base_url=f"{session.url}/rest",
        headers={"Authorization": f"Bearer {session.token}"},
        timeout=30,
    )
