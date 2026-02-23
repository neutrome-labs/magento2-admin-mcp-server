"""execute tool — run TypeScript in a workerd sandbox with full Magento API access.

Only present in Mode 2 (``FEATURED_APIS_ALL=true``). The LLM writes a TS
expression; workerd receives ``fetchMagento()`` + ``OPENAPI_SCHEMA`` bindings.
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp.dependencies import Depends

from src.dependencies import get_magento_session, get_openapi_schema
from src.magento.client import MagentoSession
from src.workerd.runner import run_in_workerd


async def execute(
    expression: str,
    session: MagentoSession = Depends(get_magento_session),
    schema: dict[str, Any] = Depends(get_openapi_schema),
) -> str:
    """Execute a TypeScript expression inside an isolated workerd sandbox.

    The sandbox has access to:
    - ``fetchMagento(method, path, body?)`` — pre-authenticated Magento REST helper
    - ``OPENAPI_SCHEMA`` — the full Magento OpenAPI spec as a JS object

    Args:
        expression: A TypeScript expression to evaluate. Must return a value
                    (the last expression's result is captured as JSON).
        session: Injected Magento session (auto-decrypted from JWT).
        schema: Injected OpenAPI schema (cached).

    Returns:
        JSON string with the execution result or error.
    """
    try:
        result = await run_in_workerd(expression, session, schema)
        return result
    except TimeoutError:
        return json.dumps({"error": "Execution timed out"})
    except Exception as exc:
        return json.dumps({"error": f"Execution failed: {exc}"})
