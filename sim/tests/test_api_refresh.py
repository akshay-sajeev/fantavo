"""Tests for POST /league/{league_id}/refresh -- the manual, on-demand
counterpart to the daily Cron-triggered /internal/reingest +
/internal/precompute pair (see docs/decisions.md's Vercel Serverless
Migration entry, and docs/superpowers/specs/2026-08-19-manual-league-
refresh-design.md). Same assertion style as sim/tests/test_api_precompute.py
and test_reingest.py -- real DB-observable side effects, not mock
call-counts."""

from __future__ import annotations

import copy
import os
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import DEFAULT_TEST_DSN
from ingest.espn_client import EspnFetchError
from sim.api import app as app_module
from sim.api import reingest
from sim.api.cache import read_cached_simulation
from sim.tests.conftest import ConnectedClient

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)


@pytest.fixture()
def client(
    pg_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Same shape as sim/tests/test_api_app.py's own `client` fixture."""
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    monkeypatch.setattr(app_module, "start_scheduler", lambda dsn=None: None)
    with TestClient(app_module.app) as test_client:
        yield test_client


def test_refresh_requires_league_ownership(
    connect_as: Callable[[dict[str, Any]], ConnectedClient], raw_fixture: dict[str, Any]
) -> None:
    # require_league_owner runs before any of this route's own logic --
    # an arbitrary league_id this caller never connected 403s immediately,
    # never reaching the cooldown check or reingest_user.
    cc = connect_as(raw_fixture)
    response = cc.client.post("/league/424242/refresh", headers=cc.headers)
    assert response.status_code == 403


def test_refresh_requires_auth(client: TestClient) -> None:
    response = client.post("/league/424242/refresh")
    assert response.status_code == 401


def test_refresh_blocks_a_second_call_within_the_cooldown(
    connect_as: Callable[[dict[str, Any]], ConnectedClient],
    raw_fixture: dict[str, Any],
) -> None:
    # connect_as's own POST /leagues/connect already performed a real
    # ingest moments ago (real wall-clock time), so league.ingested_at is
    # already well within the 5-minute cooldown window -- no extra setup
    # needed to prove the very next refresh call is blocked. The route
    # returns 429 before ever calling reingest_user, so no need to
    # monkeypatch reingest.fetch_live_league here.
    cc = connect_as(raw_fixture)
    response = cc.client.post(f"/league/{cc.league_id}/refresh", headers=cc.headers)
    assert response.status_code == 429
    retry_after = int(response.headers["Retry-After"])
    assert 0 < retry_after <= 300


def test_refresh_succeeds_past_the_cooldown_and_updates_the_cache(
    connect_as: Callable[[dict[str, Any]], ConnectedClient],
    raw_fixture: dict[str, Any],
    pg_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: raw_fixture)
    cc = connect_as(raw_fixture)

    # Backdate ingested_at past the cooldown window -- the real
    # DB-observable precondition the route's cooldown check reads.
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    pg_conn.execute(
        "UPDATE league SET ingested_at = %s WHERE league_id = %s AND season_id = %s",
        (old, raw_fixture["id"], raw_fixture["seasonId"]),
    )
    pg_conn.commit()

    response = cc.client.post(f"/league/{cc.league_id}/refresh", headers=cc.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["odds_updated"] is True
    assert body["ingested_at"] is not None

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ingested_at FROM league WHERE league_id = %s AND season_id = %s",
            (raw_fixture["id"], raw_fixture["seasonId"]),
        )
        row = cur.fetchone()
    assert row is not None
    (ingested_at,) = row
    assert ingested_at > old

    cached = read_cached_simulation(pg_conn, raw_fixture["id"], raw_fixture["seasonId"])
    assert cached is not None
    assert cached.n_sims > 0


def test_refresh_maps_espn_fetch_error_to_502(
    connect_as: Callable[[dict[str, Any]], ConnectedClient],
    raw_fixture: dict[str, Any],
    pg_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: raw_fixture)
    cc = connect_as(raw_fixture)

    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    pg_conn.execute(
        "UPDATE league SET ingested_at = %s WHERE league_id = %s AND season_id = %s",
        (old, raw_fixture["id"], raw_fixture["seasonId"]),
    )
    pg_conn.commit()

    def _fail(*args: Any, **kwargs: Any) -> Any:
        raise EspnFetchError("simulated ESPN outage")

    monkeypatch.setattr(reingest, "fetch_live_league", _fail)

    response = cc.client.post(f"/league/{cc.league_id}/refresh", headers=cc.headers)
    assert response.status_code == 502


def test_refresh_returns_ok_with_odds_not_updated_for_a_not_yet_drafted_season(
    connect_as: Callable[[dict[str, Any]], ConnectedClient],
    raw_fixture: dict[str, Any],
    pg_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a pre-draft-shaped payload (drafted forced False, rosters cleared) is
    ingested, reingest_user succeeds (ingests the league metadata), but
    precompute_league fails because there are no rosters to compute odds for.
    This exercises the second IngestError handler: reingest succeeded but odds
    computation failed, so ingested_at is set (not None) but odds_updated is False."""
    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: raw_fixture)
    cc = connect_as(raw_fixture)

    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    pg_conn.execute(
        "UPDATE league SET ingested_at = %s WHERE league_id = %s AND season_id = %s",
        (old, raw_fixture["id"], raw_fixture["seasonId"]),
    )
    pg_conn.commit()

    pre_draft_raw = copy.deepcopy(raw_fixture)
    pre_draft_raw["draftDetail"]["drafted"] = False
    for team in pre_draft_raw["teams"]:
        team["roster"]["entries"] = []
    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: pre_draft_raw)

    response = cc.client.post(f"/league/{cc.league_id}/refresh", headers=cc.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["odds_updated"] is False
    assert body["ingested_at"] is not None

    # Verify that reingest_user actually updated the league in the database
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ingested_at FROM league WHERE league_id = %s AND season_id = %s",
            (raw_fixture["id"], raw_fixture["seasonId"]),
        )
        row = cur.fetchone()
    assert row is not None
    (new_ingested_at,) = row
    assert new_ingested_at > old


def test_refresh_returns_ok_with_odds_not_updated_when_precompute_fails(
    connect_as: Callable[[dict[str, Any]], ConnectedClient],
    raw_fixture: dict[str, Any],
    pg_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercises the second IngestError handler: reingest_user succeeds
    (real data ingested, league.ingested_at updated), but precompute_league
    raises IngestError. The response should show that ingestion succeeded
    (ingested_at is not None) but odds computation failed (odds_updated=False).
    Same handler as test_refresh_returns_ok_with_odds_not_updated_for_a_not_yet_
    drafted_season, just triggered by a different underlying failure (a raw
    monkeypatched IngestError here, vs. a real no-rosters-to-compute-odds-for
    condition there)."""
    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: raw_fixture)
    cc = connect_as(raw_fixture)

    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    pg_conn.execute(
        "UPDATE league SET ingested_at = %s WHERE league_id = %s AND season_id = %s",
        (old, raw_fixture["id"], raw_fixture["seasonId"]),
    )
    pg_conn.commit()

    # Monkeypatch precompute_league to raise IngestError, simulating a scenario
    # where reingest succeeds but precompute fails (e.g., a logic error in
    # computing odds from the freshly-ingested data).
    from ingest.errors import IngestError

    def _precompute_fails(*args: Any, **kwargs: Any) -> Any:
        raise IngestError("simulated precompute failure")

    monkeypatch.setattr(app_module, "precompute_league", _precompute_fails)

    response = cc.client.post(f"/league/{cc.league_id}/refresh", headers=cc.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["odds_updated"] is False
    assert body["ingested_at"] is not None

    # Verify that reingest_user actually updated the league in the database
    # (ingested_at should be much more recent than the old value we set).
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ingested_at FROM league WHERE league_id = %s AND season_id = %s",
            (raw_fixture["id"], raw_fixture["seasonId"]),
        )
        row = cur.fetchone()
    assert row is not None
    (new_ingested_at,) = row
    assert new_ingested_at > old


def test_refresh_returns_ok_with_odds_not_updated_when_reingest_fails(
    connect_as: Callable[[dict[str, Any]], ConnectedClient],
    raw_fixture: dict[str, Any],
    pg_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercises the first IngestError handler: reingest_user raises IngestError,
    so nothing is ingested and league.ingested_at remains unchanged. The response
    has status="ok" (the route succeeded), odds_updated=False (no computation
    attempted), and ingested_at=None (no data was updated).

    This contrasts with test_refresh_returns_ok_with_odds_not_updated_when_precompute_fails,
    which has reingest_user succeed but precompute_league fail. Here, reingest_user
    itself fails, so the database is not modified."""
    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: raw_fixture)
    cc = connect_as(raw_fixture)

    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    pg_conn.execute(
        "UPDATE league SET ingested_at = %s WHERE league_id = %s AND season_id = %s",
        (old, raw_fixture["id"], raw_fixture["seasonId"]),
    )
    pg_conn.commit()

    # Monkeypatch reingest_user (as imported into app.py's namespace) to raise
    # IngestError, simulating a scenario where reingest cannot proceed
    # (e.g. credential decryption error that wasn't caught earlier, or
    # an ESPN payload that can't be parsed as a league).
    from ingest.errors import IngestError

    def _reingest_fails(*args: Any, **kwargs: Any) -> Any:
        raise IngestError("simulated reingest failure")

    monkeypatch.setattr(app_module, "reingest_user", _reingest_fails)

    response = cc.client.post(f"/league/{cc.league_id}/refresh", headers=cc.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["odds_updated"] is False
    assert body["ingested_at"] is None

    # Verify that reingest_user never executed — league.ingested_at in the database
    # is still the old value (unchanged). This is the key difference from the other
    # two IngestError tests, which have reingest_user actually run and update the database.
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ingested_at FROM league WHERE league_id = %s AND season_id = %s",
            (raw_fixture["id"], raw_fixture["seasonId"]),
        )
        row = cur.fetchone()
    assert row is not None
    (db_ingested_at,) = row
    assert db_ingested_at == old
