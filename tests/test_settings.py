"""Tests for Settings — configuration validation."""

from __future__ import annotations

import pytest


class TestSettingsValidation:
    def test_aes_secret_min_length(self) -> None:
        from pydantic import ValidationError

        from src.settings import Settings

        with pytest.raises(ValidationError, match="at least 32"):
            Settings(aes_secret="short")  # type: ignore[call-arg]

    def test_valid_settings(self) -> None:
        from src.settings import Settings

        s = Settings(aes_secret="a" * 32)  # type: ignore[call-arg]
        assert s.aes_secret == "a" * 32
        assert s.host == "0.0.0.0"
        assert s.port == 8000

    def test_effective_base_url_default(self) -> None:
        from src.settings import Settings

        s = Settings(aes_secret="a" * 32)  # type: ignore[call-arg]
        assert s.effective_base_url == f"http://localhost:{s.port}"

    def test_effective_base_url_explicit(self) -> None:
        from src.settings import Settings

        s = Settings(aes_secret="a" * 32, base_url="https://mcp.example.com")  # type: ignore[call-arg]
        assert s.effective_base_url == "https://mcp.example.com"

    def test_effective_base_url_strips_trailing_slash(self) -> None:
        from src.settings import Settings

        s = Settings(aes_secret="a" * 32, base_url="https://mcp.example.com/")  # type: ignore[call-arg]
        assert s.effective_base_url == "https://mcp.example.com"

    def test_defaults(self) -> None:
        from src.settings import Settings

        s = Settings(aes_secret="a" * 32)  # type: ignore[call-arg]
        assert s.rate_limit_rps == 10.0
        assert s.max_response_bytes == 5_242_880
        assert s.schema_cache_ttl_s == 300
        assert s.log_level == "INFO"
        assert s.debug is False
        assert s.workerd_timeout_ms == 30_000
        assert s.workerd_max_memory_mb == 128
