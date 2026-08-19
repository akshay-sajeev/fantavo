"""Tests for sim.api.roster_view and the GET /league/{id}/roster route.

Focused on the two things this module adds beyond a straight passthrough of
already-known data: floor/ceiling as a genuine property of each player's
sampling distribution (not just "close to mean"), and the risk_rating /
positional_concentration formulas actually reacting to availability and
bench depth as documented.
"""

from __future__ import annotations

import copy
import os
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import DEFAULT_TEST_DSN, ingest_league
from ingest.errors import MissingProjectionError
from sim.api import app as app_module
from sim.api.roster_view import _BENCH_DEPTH_RELEVANT_POSITIONS, load_team_rosters
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


def test_get_roster_returns_403_for_a_league_the_caller_does_not_own(
    connect_as: Callable[[dict[str, Any]], ConnectedClient], raw_fixture: dict[str, Any]
) -> None:
    cc = connect_as(raw_fixture)
    response = cc.client.get("/league/424242/roster", headers=cc.headers)
    assert response.status_code == 403


def test_get_roster_requires_auth(client: TestClient) -> None:
    response = client.get("/league/424242/roster")
    assert response.status_code == 401


def test_load_team_rosters_floor_is_below_mean_and_ceiling_above(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    season_id = raw_fixture["seasonId"]
    rosters = load_team_rosters(pg_conn, synthetic_league_id, season_id)
    assert rosters
    for team in rosters:
        for player in team.starters:
            # Starters are never unprojected (load_team_rosters hard-raises
            # otherwise) -- asserted explicitly so a real violation of that
            # contract fails loudly here rather than as a type error.
            assert player.has_projection
            assert player.floor is not None
            assert player.mean is not None
            assert player.ceiling is not None
            assert player.availability is not None
            assert player.floor < player.mean < player.ceiling
            assert 0.0 < player.availability <= 1.0


def test_get_roster_matches_a_direct_load_team_rosters_call(
    client: TestClient,
    pg_conn: psycopg.Connection[Any],
    synthetic_league_id: int,
    raw_fixture: dict[str, Any],
    connect_as: Callable[[dict[str, Any]], ConnectedClient],
) -> None:
    from scripts.ingest_synthetic_league import build_synthetic_raw_payload

    season_id = raw_fixture["seasonId"]
    direct = load_team_rosters(pg_conn, synthetic_league_id, season_id)

    cc = connect_as(build_synthetic_raw_payload(raw_fixture))
    response = cc.client.get(
        f"/league/{synthetic_league_id}/roster",
        params={"season_id": season_id},
        headers=cc.headers,
    )
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


def test_positional_concentration_flags_only_positions_with_zero_bench_depth(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    """Since Phase 6, `scripts/ingest_synthetic_league.py` also drafts real
    bench players (see docs/decisions.md) -- so this no longer flags every
    starting position for every team. Assert the formula's actual
    documented rule (flag a QB/RB/WR/TE starting position iff zero
    same-position bench players -- K/D-ST are never flagged, see
    `_BENCH_DEPTH_RELEVANT_POSITIONS`) against whatever bench composition
    this run's random-but-deterministic-per-test-seed draft happened to
    produce, rather than a fixed hand-computed set.
    """
    season_id = raw_fixture["seasonId"]
    rosters = load_team_rosters(pg_conn, synthetic_league_id, season_id)
    assert any(team.bench for team in rosters)  # bench depth now genuinely exists
    for team in rosters:
        starter_positions = {
            p.position for p in team.starters if p.position in _BENCH_DEPTH_RELEVANT_POSITIONS
        }
        bench_positions = {p.position for p in team.bench}
        assert set(team.positional_concentration) == {
            f"No bench depth at {pos}" for pos in starter_positions if pos not in bench_positions
        }


def test_positional_concentration_never_flags_kicker_or_d_st(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    """K and D/ST are single-slot positions a real roster normally carries
    exactly one of -- zero bench depth there is expected, not a
    concentration risk (see `_positional_concentration`'s docstring)."""
    season_id = raw_fixture["seasonId"]
    rosters = load_team_rosters(pg_conn, synthetic_league_id, season_id)
    for team in rosters:
        assert "No bench depth at K" not in team.positional_concentration
        assert "No bench depth at D/ST" not in team.positional_concentration


# --------------------------------------------------------------------------
# Unprojected roster players -- real, drafted rosters occasionally include
# one (see docs/decisions.md, "no more mock data"); the fabricated
# SYNTHETIC league never does, since its mock draft only ever picks
# already-projectable players. James Conner is a real, currently-existing
# case in fixtures/league_raw_2026.json: rostered on the bench, but his
# 2026 season-total stat block has appliedTotal == 0 (see docs/decisions.md
# Phase 1's "known bad players").
# --------------------------------------------------------------------------


def test_load_team_rosters_renders_a_real_unprojected_bench_player_without_failing(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]
) -> None:
    ingest_league(pg_conn, raw_fixture, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    pg_conn.commit()

    rosters = load_team_rosters(pg_conn, raw_fixture["id"], raw_fixture["seasonId"])

    unprojected = [p for team in rosters for p in team.bench if not p.has_projection]
    assert any(p.name == "James Conner" for p in unprojected)
    conner = next(p for p in unprojected if p.name == "James Conner")
    assert conner.is_starter is False
    assert conner.mean is None
    assert conner.sd is None
    assert conner.availability is None
    assert conner.floor is None
    assert conner.ceiling is None
    # A real position label, not "?" -- comes from the raw fixture directly
    # (ingest.parse.every_player_with_stats), not from the projection pool
    # that has no entry for him.
    assert conner.position != "?"

    # The rest of the league is unaffected: every other team still loads,
    # and risk_rating/positional_concentration (starters-only formulas) are
    # still real numbers, not skipped or zeroed out because of one
    # unrelated team's unprojected bench player.
    assert len(rosters) == len(raw_fixture["teams"])
    for team in rosters:
        assert team.starters  # every real team has a full starting lineup
        assert isinstance(team.risk_rating, float)


def test_load_team_rosters_raises_for_an_unprojected_starter(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]
) -> None:
    """Unlike a bench player, an unprojected *starter* still hard-fails --
    it is exactly as load-bearing here as in ingest.parse.build_team_params.
    Constructed by moving the real James Conner entry (see the test above)
    from bench into a starting slot; nothing else about the fixture needs
    to be fabricated since he already has no usable projection for real."""
    raw = copy.deepcopy(raw_fixture)
    moved = False
    for team in raw["teams"]:
        for entry in team["roster"]["entries"]:
            if entry["playerId"] == 3045147:  # James Conner
                entry["lineupSlotId"] = 2  # RB, a starting slot
                moved = True
    assert moved, "fixture no longer contains player 3045147 (James Conner) -- update this test"

    ingest_league(pg_conn, raw, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    pg_conn.commit()

    with pytest.raises(MissingProjectionError, match="no usable projection"):
        load_team_rosters(pg_conn, raw["id"], raw["seasonId"])
