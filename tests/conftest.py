"""Shared fixtures for the Magento 2 Admin MCP Server test suite."""

from __future__ import annotations

import os

import pytest

# ── Ensure AES_SECRET is set before any Settings import ─────────────────
TEST_AES_SECRET = "test-secret-0123456789abcdef0123456789abcdef"

os.environ.setdefault("AES_SECRET", TEST_AES_SECRET)


@pytest.fixture
def aes_secret() -> str:
    return TEST_AES_SECRET
