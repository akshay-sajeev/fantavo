"""Tests for sim.api.schedule_view and the GET /league/{id}/schedule route.

The load-bearing behavior to verify: current_week is derived purely from
the matchup table's own `winner` column, never fabricated -- a freshly
ingested (all-UNDECIDED) synthetic league reports week 1 as current, and a
schedule with every matchup decided reports no current week at all.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import DEFAULT_TEST_DSN
from scripts.ingest_synthetic_league import build_synthetic_raw_payload
from sim.api import app as app_module
from sim.api.schedule_view import load_schedule
from sim.tests.conftest import ConnectedClient

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)


@pytest.fixture()
def client(
    pg_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    monkeypatch.setattr(app_module, "start_scheduler", lambda dsn=None: None)
    with TestClient(app_module.app) as test_client:
        yield test_client


def test_get_schedule_returns_403_for_a_league_the_caller_does_not_own(
    connect_as: Callable[[dict[str, Any]], ConnectedClient], raw_fixture: dict[str, Any]
) -> None:
    cc = connect_as(raw_fixture)
    response = cc.client.get("/league/424242/schedule", headers=cc.headers)
    assert response.status_code == 403


def test_get_schedule_requires_auth(client: TestClient) -> None:
    response = client.get("/league/424242/schedule")
    assert response.status_code == 401


def test_load_schedule_reports_week_1_as_current_for_an_all_undecided_league(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    season_id = raw_fixture["seasonId"]
    schedule = load_schedule(pg_conn, synthetic_league_id, season_id)
    assert schedule.n_regular_weeks == 14
    assert schedule.current_week == 1
    assert len(schedule.weeks) == 14
    # Every synthetic matchup is genuinely unplayed (winner == "UNDECIDED"),
    # per scripts/ingest_synthetic_league.py -- never render a decided score
    # for a game that hasn't happened.
    for week in schedule.weeks:
        for matchup in week:
            assert matchup.winner == "UNDECIDED"


def test_load_schedule_reports_no_current_week_once_every_matchup_is_decided(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    season_id = raw_fixture["seasonId"]
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE matchup SET winner = 'HOME' WHERE league_id = %s AND season_id = %s",
            (synthetic_league_id, season_id),
        )
    pg_conn.commit()

    schedule = load_schedule(pg_conn, synthetic_league_id, season_id)
    assert schedule.current_week is None


def test_get_schedule_route_matches_a_direct_load_schedule_call(
    client: TestClient,
    pg_conn: psycopg.Connection[Any],
    synthetic_league_id: int,
    raw_fixture: dict[str, Any],
    connect_as: Callable[[dict[str, Any]], ConnectedClient],
) -> None:
    season_id = raw_fixture["seasonId"]
    direct = load_schedule(pg_conn, synthetic_league_id, season_id)

    cc = connect_as(build_synthetic_raw_payload(raw_fixture))
    response = cc.client.get(
        f"/league/{synthetic_league_id}/schedule",
        params={"season_id": season_id},
        headers=cc.headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["n_regular_weeks"] == direct.n_regular_weeks
    assert body["current_week"] == direct.current_week
    assert len(body["weeks"]) == len(direct.weeks)
    assert len(body["weeks"][0]) == len(direct.weeks[0]) == 5  # 10 teams -> 5 matchups/week
