"""Tests for OpenAPI schema filtering and extraction."""

from __future__ import annotations

import pytest

from src.magento.schema import _collect_refs, extract_relevant_paths

# ── Fixture: minimal OpenAPI schema ────────────────────────────────────

@pytest.fixture
def sample_schema() -> dict:
    return {
        "info": {"title": "Magento", "version": "2.4"},
        "basePath": "/rest",
        "paths": {
            "/V1/orders": {
                "get": {
                    "summary": "Lists orders matching criteria",
                    "operationId": "salesOrderRepositoryV1GetListGet",
                    "tags": ["salesOrderRepositoryV1"],
                    "parameters": [
                        {"$ref": "#/definitions/searchCriteriaParam"},
                    ],
                },
            },
            "/V1/orders/{id}": {
                "get": {
                    "summary": "Loads a specified order",
                    "operationId": "salesOrderRepositoryV1GetGet",
                    "tags": ["salesOrderRepositoryV1"],
                    "responses": {
                        "200": {
                            "schema": {"$ref": "#/definitions/sales-data-order-interface"},
                        },
                    },
                },
            },
            "/V1/products": {
                "get": {
                    "summary": "Get product list",
                    "operationId": "catalogProductRepositoryV1GetListGet",
                    "tags": ["catalogProductRepositoryV1"],
                },
            },
            "/V1/products/{sku}": {
                "get": {
                    "summary": "Get info about product by SKU",
                    "operationId": "catalogProductRepositoryV1GetGet",
                    "tags": ["catalogProductRepositoryV1"],
                    "responses": {
                        "200": {
                            "schema": {"$ref": "#/definitions/catalog-data-product-interface"},
                        },
                    },
                },
                "put": {
                    "summary": "Create or update product",
                    "operationId": "catalogProductRepositoryV1SavePut",
                    "tags": ["catalogProductRepositoryV1"],
                },
            },
            "/V1/customers/search": {
                "get": {
                    "summary": "Search customers",
                    "operationId": "customerGroupRepositoryV1GetListGet",
                    "tags": ["customerAccountManagementV1"],
                },
            },
        },
        "definitions": {
            "searchCriteriaParam": {"type": "object", "properties": {}},
            "sales-data-order-interface": {
                "type": "object",
                "properties": {"entity_id": {"type": "integer"}},
            },
            "catalog-data-product-interface": {
                "type": "object",
                "properties": {"sku": {"type": "string"}},
            },
            "unused-definition": {"type": "object"},
        },
    }


# ── Exact match ─────────────────────────────────────────────────────────


class TestExactMatch:
    def test_exact_path(self, sample_schema: dict) -> None:
        result = extract_relevant_paths(sample_schema, "/V1/orders")
        assert "/V1/orders" in result["paths"]

    def test_exact_path_with_param(self, sample_schema: dict) -> None:
        result = extract_relevant_paths(sample_schema, "/V1/orders/{id}")
        assert "/V1/orders/{id}" in result["paths"]

    def test_case_insensitive(self, sample_schema: dict) -> None:
        result = extract_relevant_paths(sample_schema, "/v1/orders")
        assert len(result["paths"]) >= 1


# ── Prefix match ────────────────────────────────────────────────────────


class TestPrefixMatch:
    def test_prefix_matches_children(self, sample_schema: dict) -> None:
        result = extract_relevant_paths(sample_schema, "/V1/products")
        # Should match /V1/products and /V1/products/{sku}
        assert "/V1/products" in result["paths"]
        assert "/V1/products/{sku}" in result["paths"]

    def test_prefix_orders(self, sample_schema: dict) -> None:
        result = extract_relevant_paths(sample_schema, "/V1/orders")
        assert "/V1/orders" in result["paths"]
        assert "/V1/orders/{id}" in result["paths"]


# ── Keyword search ──────────────────────────────────────────────────────


class TestKeywordSearch:
    def test_keyword_in_summary(self, sample_schema: dict) -> None:
        result = extract_relevant_paths(sample_schema, "product")
        paths = result["paths"]
        assert "/V1/products" in paths or "/V1/products/{sku}" in paths

    def test_multi_keyword(self, sample_schema: dict) -> None:
        result = extract_relevant_paths(sample_schema, "catalog product")
        paths = result["paths"]
        assert len(paths) >= 1

    def test_keyword_no_match(self, sample_schema: dict) -> None:
        result = extract_relevant_paths(sample_schema, "nonexistent endpoint")
        assert len(result["paths"]) == 0

    def test_keyword_in_tags(self, sample_schema: dict) -> None:
        result = extract_relevant_paths(sample_schema, "customerAccountManagement")
        assert "/V1/customers/search" in result["paths"]


# ── Referenced definitions ──────────────────────────────────────────────


class TestDefinitions:
    def test_includes_referenced_defs(self, sample_schema: dict) -> None:
        result = extract_relevant_paths(sample_schema, "/V1/orders/{id}")
        defs = result.get("definitions", {})
        assert "sales-data-order-interface" in defs

    def test_excludes_unused_defs(self, sample_schema: dict) -> None:
        result = extract_relevant_paths(sample_schema, "/V1/orders/{id}")
        defs = result.get("definitions", {})
        assert "unused-definition" not in defs
        assert "catalog-data-product-interface" not in defs

    def test_collect_refs_nested(self) -> None:
        obj = {
            "responses": {
                "200": {
                    "schema": {
                        "type": "array",
                        "items": {"$ref": "#/definitions/order-item"},
                    },
                },
            },
        }
        refs = _collect_refs(obj)
        assert "order-item" in refs


# ── Edge cases ──────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_schema(self) -> None:
        result = extract_relevant_paths({}, "anything")
        assert result["paths"] == {}

    def test_none_schema(self) -> None:
        result = extract_relevant_paths(None, "anything")  # type: ignore
        assert "paths" in result

    def test_empty_query(self, sample_schema: dict) -> None:
        result = extract_relevant_paths(sample_schema, "")
        # Empty string matches everything via prefix
        assert len(result["paths"]) > 0

    def test_preserves_basepath(self, sample_schema: dict) -> None:
        result = extract_relevant_paths(sample_schema, "/V1/orders")
        assert result["basePath"] == "/rest"
