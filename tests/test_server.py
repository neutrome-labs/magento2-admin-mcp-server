"""Tests for the server factory — verify single-tool registration."""

from __future__ import annotations

import asyncio

import pytest


class TestServerFactory:
    def test_creates_fastmcp_instance(self) -> None:
        from src.server import create_server

        mcp = create_server()
        assert mcp.name == "Magento 2 Admin MCP"

    @pytest.mark.asyncio
    async def test_registers_eval_in_workerd_tool(self) -> None:
        from src.server import create_server

        mcp = create_server()
        tools = await mcp.list_tools()
        tool_names = [t.name for t in tools]
        assert "eval_in_workerd" in tool_names
        assert len(tool_names) == 1

    def test_magento_session_dataclass(self) -> None:
        from src.server import MagentoSession

        s = MagentoSession(url="https://m2.test", token="abc")
        assert s.url == "https://m2.test"
        assert s.token == "abc"


def test_token_preflight_allows_content_type() -> None:
    """Browser OPTIONS preflight against /token should succeed with CORS headers."""
    from starlette.testclient import TestClient
    from src.server import app

    client = TestClient(app)
    headers = {
        "Origin": "http://example.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }
    resp = client.options("/token", headers=headers)
    assert resp.status_code == 200
    allow = resp.headers.get("access-control-allow-headers", "")
    assert "content-type" in allow.lower() or allow.strip() == "*"