"""Tests for the two /internal/* routes (sim/api/app.py) that Vercel Cron
Jobs call in place of sim/api/scheduler.py's in-process interval jobs on a
serverless deploy (see docs/decisions.md's Vercel Serverless Migration
entry). Same assertion style as sim/tests/test_api_precompute.py and
test_reingest.py -- a real DB-observable side effect, not a mock
call-count, proves the underlying function actually ran."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import DEFAULT_TEST_DSN
from sim.api import app as app_module
from sim.api import auth_view, league_connection_view, reingest
from sim.api.cache import read_cached_simulation

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)

# A deliberately old fixed instant for the initial connect -- proves the
# reingest test below actually moved ingested_at forward, rather than just
# finding a row that was already there.
FIXED_CONNECT_AT = datetime(2020, 1, 1, tzinfo=timezone.utc)


@pytest.fixture()
def client(
    pg_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Same shape as sim/tests/test_api_app.py's own `client` fixture --
    see that module's docstring for why the scheduler is stubbed out and
    why `pg_conn` is requested even though unused directly here."""
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    monkeypatch.setattr(app_module, "start_scheduler", lambda dsn=None: None)
    with TestClient(app_module.app) as test_client:
        yield test_client


def test_trigger_precompute_401s_with_no_authorization_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CRON_SECRET", raising=False)
    response = client.post("/internal/precompute")
    assert response.status_code == 401


def test_trigger_precompute_401s_with_the_wrong_secret(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRON_SECRET", "a-real-cron-secret")
    response = client.post(
        "/internal/precompute", headers={"Authorization": "Bearer wrong-secret"}
    )
    assert response.status_code == 401


def test_trigger_precompute_caches_the_synthetic_league(
    client: TestClient,
    pg_conn: psycopg.Connection[Any],
    synthetic_league_id: int,
    raw_fixture: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRON_SECRET", "a-real-cron-secret")
    response = client.post(
        "/internal/precompute", headers={"Authorization": "Bearer a-real-cron-secret"}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    cached = read_cached_simulation(pg_conn, synthetic_league_id, raw_fixture["seasonId"])
    assert cached is not None
    assert cached.n_sims > 0


def test_trigger_reingest_401s_with_no_authorization_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CRON_SECRET", raising=False)
    response = client.post("/internal/reingest")
    assert response.status_code == 401


def test_trigger_reingest_updates_ingested_at_for_a_connected_user(
    client: TestClient,
    pg_conn: psycopg.Connection[Any],
    raw_fixture: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    user = auth_view.create_user(
        pg_conn, "cronreingest@example.com", "a-real-password", FIXED_CONNECT_AT
    )
    league_connection_view.connect_league(
        pg_conn, user.user_id, raw_fixture["id"], None, None, FIXED_CONNECT_AT
    )
    pg_conn.commit()

    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: raw_fixture)
    monkeypatch.setenv("CRON_SECRET", "a-real-cron-secret")

    response = client.post(
        "/internal/reingest", headers={"Authorization": "Bearer a-real-cron-secret"}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ingested_at FROM league WHERE league_id = %s AND season_id = %s",
            (raw_fixture["id"], raw_fixture["seasonId"]),
        )
        row = cur.fetchone()
    assert row is not None
    (ingested_at,) = row
    assert ingested_at > FIXED_CONNECT_AT
