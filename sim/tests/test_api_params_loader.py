"""Tests for sim.api.params_loader against a real local Postgres instance
(see sim/tests/conftest.py for the skip-if-unreachable `pg_conn` fixture and
the `synthetic_league_id` fixture that ingests the SYNTHETIC validation
league through the real ingest.db.ingest_league path).
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from ingest.parse import parse_player_pool, parse_scoring_table
from sim.api.params_loader import LeagueNotIngestedError, load_league, resolve_season_id
from sim.engine import round_robin_schedule
from sim.params.derive import derive_player_params_pool
from sim.params.mock_rosters import build_mock_league
from sim.params.variance import fit_position_cv


def test_load_league_raises_for_an_uningested_league(pg_conn: psycopg.Connection[Any]) -> None:
    with pytest.raises(LeagueNotIngestedError):
        load_league(pg_conn, 424242, 2026)


def test_resolve_season_id_raises_for_an_unknown_league(pg_conn: psycopg.Connection[Any]) -> None:
    with pytest.raises(LeagueNotIngestedError):
        resolve_season_id(pg_conn, 424242, None)


def test_resolve_season_id_defaults_to_the_latest_ingested_season(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    season_id = resolve_season_id(pg_conn, synthetic_league_id, None)
    assert season_id == raw_fixture["seasonId"]


def test_resolve_season_id_passes_through_an_explicit_season_id(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    assert resolve_season_id(pg_conn, synthetic_league_id, raw_fixture["seasonId"]) == raw_fixture[
        "seasonId"
    ]


def test_load_league_matches_build_mock_league_rosters_exactly(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    """The DB-backed load path (ingest.parse.build_team_params against the
    raw_payload stored by a real ingest_league() call) must reconstruct the
    exact same rosters sim.params.mock_rosters.build_mock_league builds
    in-memory from the same underlying draft. This is the load-bearing
    property behind Phase 4's synthetic-league verification: ingesting the
    mock league through the real DB layer and reading it back must be
    lossless.
    """
    season_id = raw_fixture["seasonId"]
    loaded = load_league(pg_conn, synthetic_league_id, season_id)

    scoring_table = parse_scoring_table(raw_fixture)
    player_pool, _skipped = parse_player_pool(raw_fixture, scoring_table)
    position_cv = fit_position_cv(raw_fixture)
    players_by_id = derive_player_params_pool(player_pool, position_cv)
    expected_league = build_mock_league(raw_fixture, player_pool, players_by_id, n_mock_teams=10)

    assert len(loaded.league.teams) == len(expected_league.teams) == 10
    for actual_team, expected_team in zip(loaded.league.teams, expected_league.teams, strict=True):
        assert actual_team.team_id == expected_team.team_id
        assert [p.player_id for p in actual_team.starters] == [
            p.player_id for p in expected_team.starters
        ]
        for actual_player, expected_player in zip(
            actual_team.starters, expected_team.starters, strict=True
        ):
            # pytest.approx, not ==: Postgres JSONB does not guarantee
            # bit-exact float64 round-tripping (it stores/normalizes numeric
            # literals as text, not as an IEEE754 bit pattern), so a value
            # that crosses the JSONB storage boundary can differ from the
            # original in-memory float by up to ~1 ULP. That is a real,
            # expected property of JSONB storage, not a bug in this loader --
            # rel=1e-9 is many orders tighter than anything that could affect
            # a simulated probability, while still catching a genuine
            # derivation error.
            assert actual_player.mean == pytest.approx(expected_player.mean, rel=1e-9)
            assert actual_player.sd == pytest.approx(expected_player.sd, rel=1e-9)
            assert actual_player.availability == pytest.approx(
                expected_player.availability, rel=1e-9
            )

    n_regular_weeks = raw_fixture["settings"]["scheduleSettings"]["matchupPeriodCount"]
    expected_schedule = round_robin_schedule(n_teams=10, n_weeks=n_regular_weeks)
    assert (loaded.league.schedule == expected_schedule).all()
    assert loaded.league.n_playoff_teams == expected_league.n_playoff_teams

    # players_by_id on the loaded result must be usable to resolve every
    # drafted starter -- what the what-if endpoint relies on to validate
    # roster_overrides.
    for team in loaded.league.teams:
        for player in team.starters:
            resolved = loaded.players_by_id[player.player_id]
            assert resolved.mean == player.mean
            assert resolved.sd == player.sd
            assert resolved.availability == player.availability
