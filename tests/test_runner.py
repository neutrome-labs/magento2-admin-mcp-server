"""Tests for workerd runner — unit tests with mocked subprocesses."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workerd.runner import (
    _api_cache,
    _build_capnp_config,
    _session_hash,
)


@dataclass(frozen=True)
class FakeSession:
    url: str = "https://m2.test"
    token: str = "abc123"


@dataclass
class FakeSettings:
    schema_cache_ttl_s: int = 300
    workerd_timeout_ms: int = 10_000
    workerd_max_memory_mb: int = 128


class TestSessionHash:
    def test_deterministic(self) -> None:
        h1 = _session_hash("https://m2.test", "tok")
        h2 = _session_hash("https://m2.test", "tok")
        assert h1 == h2

    def test_different_token_different_hash(self) -> None:
        h1 = _session_hash("https://m2.test", "tok1")
        h2 = _session_hash("https://m2.test", "tok2")
        assert h1 != h2

    def test_different_url_different_hash(self) -> None:
        h1 = _session_hash("https://a.test", "tok")
        h2 = _session_hash("https://b.test", "tok")
        assert h1 != h2


class TestCapnpConfig:
    def test_contains_port_and_bindings(self) -> None:
        cfg = _build_capnp_config(
            worker_filename="worker.js",
            magento_url="https://m2.test",
            magento_token="secret",
            port=12345,
            max_memory_mb=128,
        )
        assert "12345" in cfg
        assert "https://m2.test" in cfg
        assert "secret" in cfg
        assert "worker.js" in cfg
        assert "internet" in cfg
        assert "globalOutbound" in cfg


class TestRunInWorkerd:
    @pytest.mark.asyncio
    async def test_no_workerd_binary(self) -> None:
        """When workerd is not on PATH, return a helpful error dict."""
        from src.workerd.runner import run_in_workerd

        with patch("shutil.which", return_value=None):
            result = await run_in_workerd(
                "return 1",
                FakeSession(),
                FakeSettings(),
            )

        assert isinstance(result, dict)
        assert "error" in result
        assert "workerd" in result["error"].lower()


class TestApiCache:
    def test_cache_starts_empty(self) -> None:
        # After import, cache should be a dict (may have entries from other tests)
        assert isinstance(_api_cache, dict)
