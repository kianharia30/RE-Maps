"""Shared fixtures.

Tests are split into two kinds:

* pure unit tests, which need nothing but the code (address normalisation,
  currency formatting, the zoom ladder, the forecast maths);
* integration tests marked `@pytest.mark.db`, which query the real database.

The `db` marker is skipped automatically when no database is reachable, so
`pytest` is useful on a fresh clone before any data has been ingested.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("GEOCODER_MIN_INTERVAL_S", "0")


def _database_available() -> bool:
    try:
        import psycopg

        from app.config import get_settings

        with (
            psycopg.connect(get_settings().database_url, connect_timeout=3) as conn,
            conn.cursor() as cur,
        ):
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


DB_AVAILABLE = _database_available()


def _has_transactions() -> bool:
    if not DB_AVAILABLE:
        return False
    try:
        from app.db import sync_fetch_one

        row = sync_fetch_one("SELECT count(*) AS n FROM transactions LIMIT 1")
        return bool(row and row["n"] > 0)
    except Exception:
        return False


DATA_LOADED = _has_transactions()


def pytest_collection_modifyitems(config, items):
    skip_db = pytest.mark.skip(reason="no database reachable (DATABASE_URL)")
    skip_data = pytest.mark.skip(reason="database reachable but no data ingested")
    for item in items:
        if "db" in item.keywords:
            if not DB_AVAILABLE:
                item.add_marker(skip_db)
            elif not DATA_LOADED:
                item.add_marker(skip_data)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def client():
    """A TestClient over the real app, so route wiring and validation are
    exercised rather than mocked."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
