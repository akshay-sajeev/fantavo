"""Tests for sim.params.derive."""

from __future__ import annotations

from typing import Any

import pytest

from ingest.models import PlayerProjection
from ingest.parse import FIXTURES_DIR, load_fixture, parse_player_pool, parse_scoring_table
from sim.engine import PlayerParams
from sim.params.derive import derive_player_params, derive_player_params_pool
from sim.params.errors import MissingVarianceParameterError
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
def position_cv(raw: dict[str, Any]) -> dict[int, float]:
    return fit_position_cv(raw)


def test_derive_player_params_produces_valid_playerparams(
    player_pool: tuple[PlayerProjection, ...], position_cv: dict[int, float]
) -> None:
    for projection in player_pool[:20]:
        params = derive_player_params(projection, position_cv)
        assert isinstance(params, PlayerParams)
        assert params.player_id == projection.player_id
        assert params.mean == pytest.approx(projection.mean_points_per_game)
        assert params.sd > 0
        assert 0.0 < params.availability <= 1.0


def test_derive_player_params_sd_scales_with_position_cv(
    player_pool: tuple[PlayerProjection, ...], position_cv: dict[int, float]
) -> None:
    projection = player_pool[0]
    params = derive_player_params(projection, position_cv)
    expected_sd = projection.mean_points_per_game * position_cv[projection.default_position_id]
    assert params.sd == pytest.approx(expected_sd)


def test_derive_player_params_raises_for_unknown_position(
    player_pool: tuple[PlayerProjection, ...],
) -> None:
    projection = player_pool[0]
    with pytest.raises(MissingVarianceParameterError, match="no fitted coefficient"):
        derive_player_params(projection, position_cv={})


def test_derive_player_params_pool_keys_by_player_id(
    player_pool: tuple[PlayerProjection, ...], position_cv: dict[int, float]
) -> None:
    pool = derive_player_params_pool(player_pool, position_cv)
    assert len(pool) == len(player_pool)
    for projection in player_pool:
        assert pool[projection.player_id].player_id == projection.player_id
