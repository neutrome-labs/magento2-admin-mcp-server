"""fetch tool — issue a single authenticated HTTP request to any Magento REST endpoint.

Always present in Mode 1. Gives the LLM full REST access via a generic
``method + path + body`` interface.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from fastmcp.dependencies import Depends

from src.dependencies import get_magento_client

logger = logging.getLogger(__name__)

_MAX_BODY = 5 * 1024 * 1024  # 5 MB hard-cap on response body


async def fetch(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    query_params: dict[str, str] | None = None,
    client: httpx.AsyncClient = Depends(get_magento_client),
) -> str:
    """Execute a single authenticated REST request against Magento.

    Args:
        method: HTTP method (GET, POST, PUT, DELETE, PATCH).
        path: REST API path, e.g. ``/V1/orders/123``.
        body: Optional JSON body for POST/PUT/PATCH.
        query_params: Optional query-string parameters.
        client: Injected pre-authenticated httpx client.

    Returns:
        JSON response body as a string (truncated at 5 MB).
    """
    method = method.upper()
    if method not in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
        return json.dumps({"error": f"Unsupported HTTP method: {method}"})

    # Ensure path starts with /
    if not path.startswith("/"):
        path = f"/{path}"

    try:
        resp = await client.request(
            method,
            path,
            json=body,
            params=query_params,
        )
        resp.raise_for_status()

        text = resp.text
        if len(text) > _MAX_BODY:
            text = text[:_MAX_BODY] + "\n...[truncated]"
        return text

    except httpx.HTTPStatusError as exc:
        error_body = exc.response.text[:2000]
        logger.warning("Magento %s %s → %d", method, path, exc.response.status_code)
        return json.dumps({
            "error": f"HTTP {exc.response.status_code}",
            "detail": error_body,
        })
    except httpx.RequestError as exc:
        logger.error("Magento request failed: %s", exc)
        return json.dumps({"error": f"Request failed: {exc}"})
