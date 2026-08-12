"""Tests for sim.api.roster_view and the GET /league/{id}/roster route.

Focused on the two things this module adds beyond a straight passthrough of
already-known data: floor/ceiling as a genuine property of each player's
sampling distribution (not just "close to mean"), and the risk_rating /
positional_concentration formulas actually reacting to availability and
bench depth as documented.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import DEFAULT_TEST_DSN
from sim.api import app as app_module
from sim.api.roster_view import load_team_rosters

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)


@pytest.fixture()
def client(
    pg_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    monkeypatch.setattr(app_module, "start_scheduler", lambda dsn=None: None)
    with TestClient(app_module.app) as test_client:
        yield test_client


def test_get_roster_returns_404_for_an_uningested_league(client: TestClient) -> None:
    response = client.get("/league/424242/roster")
    assert response.status_code == 404


def test_load_team_rosters_floor_is_below_mean_and_ceiling_above(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    season_id = raw_fixture["seasonId"]
    rosters = load_team_rosters(pg_conn, synthetic_league_id, season_id)
    assert rosters
    for team in rosters:
        for player in team.starters:
            assert player.floor < player.mean < player.ceiling
            assert 0.0 < player.availability <= 1.0


def test_get_roster_matches_a_direct_load_team_rosters_call(
    client: TestClient,
    pg_conn: psycopg.Connection[Any],
    synthetic_league_id: int,
    raw_fixture: dict[str, Any],
) -> None:
    season_id = raw_fixture["seasonId"]
    direct = load_team_rosters(pg_conn, synthetic_league_id, season_id)

    response = client.get(f"/league/{synthetic_league_id}/roster", params={"season_id": season_id})
    assert response.status_code == 200
    body = response.json()
    assert len(body["teams"]) == len(direct) == 10

    by_id = {t.team_id: t for t in direct}
    for team_out in body["teams"]:
        team_direct = by_id[team_out["team_id"]]
        assert team_out["risk_rating"] == pytest.approx(team_direct.risk_rating)
        assert set(team_out["positional_concentration"]) == set(team_direct.positional_concentration)
        assert len(team_out["starters"]) == len(team_direct.starters)
        for player_out, player_direct in zip(
            team_out["starters"], team_direct.starters, strict=True
        ):
            assert player_out["player_id"] == player_direct.player_id
            assert player_out["floor"] == pytest.approx(player_direct.floor)
            assert player_out["ceiling"] == pytest.approx(player_direct.ceiling)


def test_team_risk_rating_is_zero_when_every_starter_is_fully_available(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    season_id = raw_fixture["seasonId"]
    rosters = load_team_rosters(pg_conn, synthetic_league_id, season_id)
    for team in rosters:
        if all(p.availability == 1.0 for p in team.starters):
            assert team.risk_rating == 0.0


def test_positional_concentration_flags_positions_with_zero_bench_depth(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    season_id = raw_fixture["seasonId"]
    rosters = load_team_rosters(pg_conn, synthetic_league_id, season_id)
    # The mock draft (sim/params/mock_rosters.py) only fills starting slots,
    # so every synthetic team has zero bench players -- every starting
    # position should be flagged for every team, a real (documented)
    # property of this fixture, not a bug in the formula.
    for team in rosters:
        assert not team.bench
        starter_positions = {p.position for p in team.starters}
        assert set(team.positional_concentration) == {
            f"No bench depth at {pos}" for pos in starter_positions
        }
