"""FastMCP assembly — build and expose the Magento 2 Admin MCP Server.

Mode selection:
- Mode 1 (default):  filter_schema + fetch + optional FEATURED_APIS_* typed tools
- Mode 2 (FEATURED_APIS_ALL=true):  execute via workerd sandbox
"""

from __future__ import annotations

import logging

from fastmcp import FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware

from src.auth.provider import StatelessOAuthProvider
from src.dependencies import init_dependencies
from src.settings import Settings

logger = logging.getLogger(__name__)


def create_server() -> FastMCP:
    """Construct and configure the FastMCP server instance."""

    settings = Settings()  # type: ignore[call-arg]

    # Initialise shared DI singletons before any tool runs
    init_dependencies(settings)

    # ── Auth provider ───────────────────────────────────────────────────
    auth = StatelessOAuthProvider(settings)

    # ── Server ──────────────────────────────────────────────────────────
    mcp = FastMCP(
        "Magento 2 Admin MCP",
        auth=auth,
    )

    # ── Middleware (order matters: first added = outermost) ─────────────
    mcp.add_middleware(ErrorHandlingMiddleware(include_traceback=settings.debug))
    mcp.add_middleware(
        RateLimitingMiddleware(max_requests_per_second=settings.rate_limit_rps)
    )
    mcp.add_middleware(
        ResponseLimitingMiddleware(max_size=settings.max_response_bytes)
    )

    # ── Mode selection ──────────────────────────────────────────────────
    if settings.featured_apis_all:
        _register_mode2(mcp)
        logger.info("Mode 2 active: execute (workerd sandbox)")
    else:
        _register_mode1(mcp, settings)
        logger.info(
            "Mode 1 active: filter_schema + fetch + %d featured tool(s)",
            len(settings.featured_apis),
        )

    # ── Health check ────────────────────────────────────────────────────
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:
        return JSONResponse({"status": "healthy", "service": "magento2-admin-mcp"})

    return mcp


# ── Mode 1: Featured Tools ─────────────────────────────────────────────


def _register_mode1(mcp: FastMCP, settings: Settings) -> None:
    """Register Mode 1 tools: filter_schema, fetch, and optional featured tools."""
    from src.tools.fetch import fetch
    from src.tools.filter_schema import filter_schema

    mcp.add_tool(filter_schema)
    mcp.add_tool(fetch)

    # Optional: typed tools from FEATURED_APIS_* env vars
    if settings.has_featured_apis:
        from src.providers.featured import build_featured_tools

        for tool in build_featured_tools(settings):
            mcp.add_tool(tool)


# ── Mode 2: Execute (workerd) ──────────────────────────────────────────


def _register_mode2(mcp: FastMCP) -> None:
    """Register Mode 2 tool: execute."""
    from src.tools.execute import execute

    mcp.add_tool(execute)


# ── Module-level ASGI app (for ``uvicorn src.server:app``) ─────────────

_mcp = create_server()
app = _mcp.http_app()
