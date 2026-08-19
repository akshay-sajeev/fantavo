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
    """Mirrors sim/tests/test_api_precompute.py's
    test_precompute_all_leagues_skips_a_league_with_no_drafted_roster
    fixture-mutation technique: a pre-draft-shaped payload (real
    scoringSettings/schedule, drafted forced False, rosters cleared) makes
    reingest_user's own ingest_league call raise RosterNotAvailableError
    (a subclass of IngestError) -- a legitimate state (e.g. a new NFL
    season that hasn't drafted yet), not a server failure."""
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
    assert body["ingested_at"] is None
