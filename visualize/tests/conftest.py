"""Pytest fixtures for the visualize backend test suite.

Sets VISUALIZE_DBINFO to the mock DBInfo file at import time so that any
``app`` module imported during the test session reads the live mock databases
(visualize_mock_prod / visualize_mock_test on 127.0.0.1:5432).
"""

import os
import sys
from pathlib import Path

# Must happen BEFORE any `app.*` import in this session. Derived from this
# file's location so the suite runs from any checkout directory.
os.environ["VISUALIZE_DBINFO"] = str(
    Path(__file__).resolve().parents[2] / "DBInfo.mock.json"
)

# Make `app` importable when pytest runs from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    """Session-scoped TestClient running the full lifespan (pools up/down)."""
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
