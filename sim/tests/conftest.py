"""Shared fixtures for sim/tests -- specifically the sim.api tests that need
a real local Postgres instance and an ingested SYNTHETIC validation league.

Reuses ingest/tests/test_db.py's exact skip-if-unreachable pattern (see that
module for the full rationale): connection info comes from
TEST_DATABASE_URL, or defaults to ingest.db.DEFAULT_TEST_DSN
(postgresql:///fantavo_test). If Postgres isn't reachable, dependent tests
skip with a clear setup message rather than failing the whole suite.

No network calls to ESPN anywhere here -- only to the local Postgres
instance and to sim.params.mock_rosters' draft over the already-saved
fixture. See scripts/ingest_synthetic_league.py for why an ingested
SYNTHETIC league is what Phase 4's tests verify against.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Any, NamedTuple

import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import DATA_TABLES, DEFAULT_TEST_DSN, ingest_league, run_migrations
from ingest.parse import FIXTURES_DIR, load_fixture
from scripts.ingest_synthetic_league import SYNTHETIC_LEAGUE_ID, build_synthetic_raw_payload
from sim.api import league_connection_view

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)
FIXTURE_PATH = FIXTURES_DIR / "league_raw_2026.json"

# simulation_cache and the 3 auth tables aren't in ingest.db.DATA_TABLES
# (none of them are written by an ingest run), so they're truncated
# alongside it here. The auth tables specifically need this: without it,
# a duplicate-email test in one test function would collide with a
# same-email signup in another.
_ALL_TABLES: tuple[str, ...] = (
    *DATA_TABLES,
    "simulation_cache",
    "app_user",
    "user_session",
    "login_throttle",
)

# A fixed instant, never datetime.now() -- see ingest/db.py and
# ingest/tests/test_db.py for why deterministic timestamps matter for
# reproducible tests in this codebase.
FIXED_INGESTED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _try_connect() -> psycopg.Connection[Any] | None:
    try:
        return psycopg.connect(TEST_DSN, autocommit=False, connect_timeout=3)
    except psycopg.OperationalError:
        return None


@pytest.fixture(scope="session")
def raw_fixture() -> dict[str, Any]:
    return load_fixture(FIXTURE_PATH)


@pytest.fixture()
def pg_conn() -> Iterator[psycopg.Connection[Any]]:
    connection = _try_connect()
    if connection is None:
        pytest.skip(
            f"no reachable Postgres at {TEST_DSN!r} -- start it with "
            "`brew services start postgresql@16` and `createdb fantavo_test` "
            "(see docs/decisions.md Phase 3 for full setup)"
        )
    run_migrations(connection)
    connection.commit()
    with connection.cursor() as cur:
        for table in _ALL_TABLES:
            cur.execute(f"TRUNCATE TABLE {table} CASCADE")
    connection.commit()
    yield connection
    connection.close()


@pytest.fixture()
def synthetic_league_id(pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]) -> int:
    """Ingest the SYNTHETIC validation league (scripts/ingest_synthetic_league.py)
    into the test database through the real `ingest.db.ingest_league` path,
    and return its league_id. Season id is always the source fixture's own
    `seasonId`.
    """
    synthetic_raw = build_synthetic_raw_payload(raw_fixture)
    assert synthetic_raw["id"] == SYNTHETIC_LEAGUE_ID
    ingest_league(pg_conn, synthetic_raw, ingested_at=FIXED_INGESTED_AT)
    pg_conn.commit()
    league_id: int = SYNTHETIC_LEAGUE_ID
    return league_id


class ConnectedClient(NamedTuple):
    """A TestClient whose bearer token belongs to a real signed-up user who
    has connected exactly the league described by the payload passed to
    connect_as() -- see that fixture."""

    client: TestClient
    headers: dict[str, str]
    league_id: int


@pytest.fixture()
def connect_as(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> Callable[[dict[str, Any]], ConnectedClient]:
    """Factory fixture: connect_as(payload) signs up a fresh user (a new
    email per call, so a test connecting two different leagues in the same
    test doesn't collide), monkeypatches fetch_live_league to return
    `payload` verbatim, connects that user to payload["id"], and returns a
    ConnectedClient. Pass `raw_fixture` for the real fixture league, or
    `build_synthetic_raw_payload(raw_fixture)` (from
    scripts.ingest_synthetic_league) for the synthetic one -- whichever
    the test under it already uses.

    Depends on the fixture literally named `client`: every test module in
    this suite defines its own local `client` fixture (see e.g.
    test_api_app.py) rather than sharing one from conftest.py, and pytest
    resolves a conftest fixture's same-named dependency using whichever
    definition is visible to the actual requesting test -- so this works
    unmodified from every existing test file without duplicating its body.
    """
    call_count = 0

    def _connect_as(payload: dict[str, Any]) -> ConnectedClient:
        nonlocal call_count
        call_count += 1
        monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: payload)
        signup_res = client.post(
            "/auth/signup",
            json={
                "email": f"connected-owner-{call_count}@example.com",
                "password": "a-real-password",
            },
        )
        headers = {"Authorization": f"Bearer {signup_res.json()['token']}"}
        connect_res = client.post(
            "/leagues/connect", json={"league_id": payload["id"]}, headers=headers
        )
        assert connect_res.status_code == 200, connect_res.text
        return ConnectedClient(client=client, headers=headers, league_id=payload["id"])

    return _connect_as
