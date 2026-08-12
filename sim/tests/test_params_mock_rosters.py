"""Tests for sim.params.mock_rosters -- the SYNTHETIC validation league
builder. These tests exercise pipeline plumbing only; they must never assert
anything that reads as a real prediction about the actual league."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from ingest.models import PlayerProjection
from ingest.parse import FIXTURES_DIR, load_fixture, parse_player_pool, parse_scoring_table
from sim.engine import LeagueParams, PlayerParams, simulate_seasons
from sim.params.derive import derive_player_params_pool
from sim.params.mock_rosters import build_mock_league
from sim.params.variance import fit_position_cv

FIXTURE_PATH = FIXTURES_DIR / "league_raw_2026.json"


@pytest.fixture(scope="module")
def raw() -> dict[str, Any]:
    return load_fixture(FIXTURE_PATH)


@pytest.fixture(scope="module")
def player_pool(raw: dict[str, Any]) -> tuple[PlayerProjection, ...]:
    table = parse_scoring_table(raw)
    pool, _ = parse_player_pool(raw, table)
    return pool


@pytest.fixture(scope="module")
def params_by_id(
    raw: dict[str, Any], player_pool: tuple[PlayerProjection, ...]
) -> dict[int, PlayerParams]:
    return derive_player_params_pool(player_pool, fit_position_cv(raw))


@pytest.fixture(scope="module")
def mock_league(
    raw: dict[str, Any],
    player_pool: tuple[PlayerProjection, ...],
    params_by_id: dict[int, PlayerParams],
) -> LeagueParams:
    return build_mock_league(raw, player_pool, params_by_id, n_mock_teams=10)


def test_mock_league_is_valid_engine_input(mock_league: LeagueParams) -> None:
    assert mock_league.n_teams == 10
    assert mock_league.n_regular_weeks > 0
    for team in mock_league.teams:
        assert len(team.starters) == 9  # QB, RB, RB, WR, WR, TE, FLEX, D/ST, K


def test_mock_league_team_names_are_unmistakably_synthetic(mock_league: LeagueParams) -> None:
    for team in mock_league.teams:
        assert "SYNTHETIC" in team.name
        assert "Mock Team" in team.name


def test_mock_league_no_player_drafted_twice(mock_league: LeagueParams) -> None:
    all_ids = [p.player_id for team in mock_league.teams for p in team.starters]
    assert len(all_ids) == len(set(all_ids))


def test_mock_league_uses_real_playerparams(
    mock_league: LeagueParams, params_by_id: dict[int, PlayerParams]
) -> None:
    """Every player on every mock team must be a *real* derived PlayerParams
    object (same mean/sd/availability sim.params.derive produced), not a
    fabricated one."""
    for team in mock_league.teams:
        for player in team.starters:
            real = params_by_id[player.player_id]
            assert player.mean == real.mean
            assert player.sd == real.sd
            assert player.availability == real.availability


def test_mock_league_runs_through_simulate_seasons(mock_league: LeagueParams) -> None:
    """The one true entry point for season outcomes -- no second simulation
    path anywhere in this module."""
    result = simulate_seasons(mock_league, n_sims=200, rng=np.random.default_rng(0))
    assert result.won_title.sum() == pytest.approx(1.0)
    assert len(result.team_names) == 10


def test_mock_league_is_deterministic(
    raw: dict[str, Any],
    player_pool: tuple[PlayerProjection, ...],
    params_by_id: dict[int, PlayerParams],
) -> None:
    """Roster assembly itself is not stochastic (only simulate_seasons is) --
    rebuilding the mock league must produce identical rosters every time."""
    a = build_mock_league(raw, player_pool, params_by_id, n_mock_teams=10)
    b = build_mock_league(raw, player_pool, params_by_id, n_mock_teams=10)
    for team_a, team_b in zip(a.teams, b.teams, strict=True):
        assert [p.player_id for p in team_a.starters] == [p.player_id for p in team_b.starters]


def test_mock_league_rejects_odd_team_count(
    raw: dict[str, Any],
    player_pool: tuple[PlayerProjection, ...],
    params_by_id: dict[int, PlayerParams],
) -> None:
    with pytest.raises(ValueError, match="must be even"):
        build_mock_league(raw, player_pool, params_by_id, n_mock_teams=9)
