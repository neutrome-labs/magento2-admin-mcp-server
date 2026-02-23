"""OpenAPI schema utilities — filtering, searching, and extraction."""

from __future__ import annotations

import re
from typing import Any


def extract_relevant_paths(schema: dict[str, Any], query: str) -> dict[str, Any]:
    """Return a filtered slice of the OpenAPI schema matching *query*.

    Matching strategy (in order of priority):
    1. Exact path match — ``/V1/orders/{id}``
    2. Path prefix match — ``/V1/orders``
    3. Keyword search — matches path segments, operation summaries, tags, and
       operationIds.

    Returns a minimal valid OpenAPI doc containing only matching paths plus
    their referenced ``#/definitions/*`` schemas.
    """
    if not schema or "paths" not in schema:
        return {"info": "No schema available", "paths": {}}

    paths = schema.get("paths", {})
    query_lower = query.lower().strip()
    keywords = re.split(r"[\s/]+", query_lower)
    keywords = [k for k in keywords if k]

    matched_paths: dict[str, Any] = {}

    for path, ops in paths.items():
        path_lower = path.lower()

        # 1. Exact match
        if path_lower == query_lower:
            matched_paths[path] = ops
            continue

        # 2. Prefix match
        if path_lower.startswith(query_lower) or query_lower.startswith(path_lower):
            matched_paths[path] = ops
            continue

        # 3. Keyword search across path, summaries, tags, operationIds
        searchable = path_lower
        if isinstance(ops, dict):
            for _method, op in ops.items():
                if isinstance(op, dict):
                    searchable += " " + op.get("summary", "").lower()
                    searchable += " " + op.get("operationId", "").lower()
                    searchable += " " + " ".join(
                        t.lower() for t in op.get("tags", [])
                    )

        if all(kw in searchable for kw in keywords):
            matched_paths[path] = ops

    # ── Collect referenced definitions ──────────────────────────────────
    definitions = schema.get("definitions", {})
    used_defs = _collect_refs(matched_paths)
    filtered_defs = {k: v for k, v in definitions.items() if k in used_defs}

    return {
        "info": schema.get("info", {}),
        "basePath": schema.get("basePath", "/rest"),
        "paths": matched_paths,
        "definitions": filtered_defs,
    }


def _collect_refs(obj: Any, _found: set[str] | None = None) -> set[str]:
    """Recursively collect all ``$ref`` definition names from an object tree."""
    if _found is None:
        _found = set()
    if isinstance(obj, dict):
        ref = obj.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/definitions/"):
            _found.add(ref.split("/")[-1])
        for v in obj.values():
            _collect_refs(v, _found)
    elif isinstance(obj, list):
        for item in obj:
            _collect_refs(item, _found)
    return _found
