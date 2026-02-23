"""Build typed tool callables from FEATURED_APIS_* env vars.

Each ``FeaturedAPI`` entry becomes a dedicated MCP tool with a proper name,
description, and parameter schema, backed by the generic ``fetch`` logic.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

import httpx
from fastmcp.dependencies import Depends
from fastmcp.tools import Tool

from src.dependencies import get_magento_client
from src.settings import FeaturedAPI, Settings

logger = logging.getLogger(__name__)


def build_featured_tools(settings: Settings) -> list[Tool]:
    """Build Tool instances from ``Settings.featured_apis`` entries.

    Each Tool wraps a closure that issues the specific HTTP request
    defined by the FeaturedAPI entry, using the Depends-injected
    pre-authenticated Magento httpx client.

    Returns:
        List of ``Tool`` instances ready for ``mcp.add_tool()``.
    """
    tools: list[Tool] = []

    for api in settings.featured_apis:
        tool_fn = _make_tool_fn(api)
        tool = Tool.from_function(
            tool_fn,
            name=api.name,
            description=api.description,
        )
        tools.append(tool)

    return tools


def _make_tool_fn(api: FeaturedAPI) -> Callable[..., Any]:
    """Create a typed async tool function for a single FeaturedAPI entry."""
    path_params = re.findall(r"\{(\w+)\}", api.path)

    async def featured_tool(
        client: httpx.AsyncClient = Depends(get_magento_client),
        **kwargs: Any,
    ) -> str:
        """Auto-generated tool for a featured Magento API endpoint."""
        path = api.path
        query_params: dict[str, str] = {}
        body: dict[str, Any] | None = None

        # Substitute path parameters
        for name in path_params:
            if name in kwargs:
                path = path.replace(f"{{{name}}}", str(kwargs.pop(name)))

        # Remaining kwargs → body (POST/PUT/PATCH) or query params (GET/DELETE)
        remaining = {k: v for k, v in kwargs.items() if v is not None}
        if api.method in ("POST", "PUT", "PATCH") and remaining:
            body = remaining
        elif remaining:
            query_params = {k: str(v) for k, v in remaining.items()}

        try:
            resp = await client.request(
                api.method,
                path,
                json=body,
                params=query_params or None,
            )
            resp.raise_for_status()
            text = resp.text
            if len(text) > 5 * 1024 * 1024:
                text = text[: 5 * 1024 * 1024] + "\n...[truncated]"
            return text
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Featured tool %s: %s %s → %d",
                api.name,
                api.method,
                path,
                exc.response.status_code,
            )
            return json.dumps(
                {
                    "error": f"HTTP {exc.response.status_code}",
                    "detail": exc.response.text[:2000],
                }
            )
        except httpx.RequestError as exc:
            return json.dumps({"error": f"Request failed: {exc}"})

    # Set function identity for FastMCP introspection
    featured_tool.__name__ = api.name
    featured_tool.__qualname__ = api.name
    featured_tool.__doc__ = api.description

    return featured_tool
