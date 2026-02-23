"""filter_schema tool — return a filtered slice of the Magento OpenAPI schema.

Always present in Mode 1. Allows the LLM to discover available endpoints
before issuing fetch calls.
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp.dependencies import Depends

from src.dependencies import get_openapi_schema
from src.magento.schema import extract_relevant_paths


async def filter_schema(
    query: str,
    schema: dict[str, Any] = Depends(get_openapi_schema),
) -> str:
    """Search the Magento REST API schema and return matching endpoints.

    Args:
        query: A search term — path prefix (``/V1/orders``), keyword
               (``catalog product``), or exact path.
        schema: Injected OpenAPI schema (cached per session).

    Returns:
        A JSON string containing matching paths, their operations,
        and referenced type definitions.
    """
    result = extract_relevant_paths(schema, query)
    path_count = len(result.get("paths", {}))

    if path_count == 0:
        return json.dumps({
            "message": f"No endpoints matching '{query}' found.",
            "hint": "Try broader keywords or check /V1/ prefix.",
        })

    # Truncate if too large
    output = json.dumps(result, indent=2)
    if len(output) > 500_000:
        # Return paths only, without full definitions
        return json.dumps({
            "info": result.get("info", {}),
            "basePath": result.get("basePath", "/rest"),
            "paths": {k: _summarize_ops(v) for k, v in result["paths"].items()},
            "note": f"Full output too large ({len(output)} bytes). Showing summaries only.",
        }, indent=2)

    return output


def _summarize_ops(ops: Any) -> Any:
    """Create a compact summary of path operations."""
    if not isinstance(ops, dict):
        return ops
    summary = {}
    for method, op in ops.items():
        if isinstance(op, dict):
            summary[method] = {
                "summary": op.get("summary", ""),
                "operationId": op.get("operationId", ""),
                "tags": op.get("tags", []),
            }
        else:
            summary[method] = op
    return summary
