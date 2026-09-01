"""Shared test fixtures for the server."""

from __future__ import annotations

import os
import pytest

# Ensure every test process sees the same bearer token so the fixture and the
# live ``app`` (created at import time) agree on what is valid.  This avoids a
# silent mismatch when ``load_settings()`` reads a persisted token from disk.
os.environ["AUTH_TOKEN"] = "test-token"


@pytest.fixture()
def auth_header():
    return {"Authorization": "Bearer test-token"}
