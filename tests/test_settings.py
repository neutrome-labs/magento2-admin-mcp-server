"""Tests for Settings — configuration validation and env var parsing."""

from __future__ import annotations

import pytest


class TestSettingsValidation:
    def test_aes_secret_min_length(self) -> None:
        """AES_SECRET must be at least 32 characters."""
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
        assert s.featured_apis_all is False

    def test_effective_base_url_default(self) -> None:
        """With no BASE_URL, effective_base_url is http://localhost:{port}."""
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


class TestFeaturedAPIParsing:
    def test_featured_apis_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FEATURED_APIS_1=name::METHOD::path should parse correctly."""
        from src.settings import Settings

        monkeypatch.setenv("FEATURED_APIS_1", "list_orders::GET::/V1/orders")
        monkeypatch.setenv("FEATURED_APIS_1_DESCRIPTION", "List all orders")

        s = Settings(aes_secret="a" * 32)  # type: ignore[call-arg]
        assert len(s.featured_apis) == 1
        api = s.featured_apis[0]
        assert api.name == "list_orders"
        assert api.method == "GET"
        assert api.path == "/V1/orders"
        assert api.description == "List all orders"

    def test_featured_apis_sorted_by_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.settings import Settings

        monkeypatch.setenv("FEATURED_APIS_10", "second::GET::/V1/b")
        monkeypatch.setenv("FEATURED_APIS_10_DESCRIPTION", "Second")
        monkeypatch.setenv("FEATURED_APIS_1", "first::GET::/V1/a")
        monkeypatch.setenv("FEATURED_APIS_1_DESCRIPTION", "First")

        s = Settings(aes_secret="a" * 32)  # type: ignore[call-arg]
        assert s.featured_apis[0].name == "first"
        assert s.featured_apis[1].name == "second"

    def test_featured_apis_bad_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pydantic import ValidationError

        from src.settings import Settings

        monkeypatch.setenv("FEATURED_APIS_1", "bad-format")  # no :: separators

        with pytest.raises(ValidationError, match="must follow"):
            Settings(aes_secret="a" * 32)  # type: ignore[call-arg]

    def test_mode2_skips_scanning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When FEATURED_APIS_ALL=true, featured_apis list stays empty."""
        from src.settings import Settings

        monkeypatch.setenv("FEATURED_APIS_ALL", "true")
        monkeypatch.setenv("FEATURED_APIS_1", "ignored::GET::/V1/x")

        s = Settings(aes_secret="a" * 32)  # type: ignore[call-arg]
        assert s.featured_apis_all is True
        assert len(s.featured_apis) == 0

    def test_has_featured_apis_property(self) -> None:
        from src.settings import Settings

        s = Settings(aes_secret="a" * 32)  # type: ignore[call-arg]
        assert s.has_featured_apis is False
