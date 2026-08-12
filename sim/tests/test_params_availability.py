"""Tests for sim.params.availability."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from ingest.parse import FIXTURES_DIR, load_fixture, parse_player_pool, parse_scoring_table
from sim.params.availability import NFL_REGULAR_SEASON_GAMES, derive_availability

FIXTURE_PATH = FIXTURES_DIR / "league_raw_2026.json"


@pytest.fixture(scope="module")
def raw() -> dict[str, Any]:
    return load_fixture(FIXTURE_PATH)


def test_nfl_regular_season_games_matches_the_fixtures_own_ceiling(raw: dict[str, Any]) -> None:
    """Cross-check the structural assumption against real data: the large
    majority of usable players should be projected for exactly
    NFL_REGULAR_SEASON_GAMES games (ESPN's own projected ceiling), not the
    fantasy league's matchupPeriodCount (14 in this fixture) -- guards
    against the exact conflation docs/decisions.md flagged in Phase 1."""
    table = parse_scoring_table(raw)
    projections, _ = parse_player_pool(raw, table)
    counts = Counter(p.games_projected for p in projections)

    assert max(counts) == NFL_REGULAR_SEASON_GAMES
    assert counts[NFL_REGULAR_SEASON_GAMES] / len(projections) > 0.9

    n_regular_weeks = raw["settings"]["scheduleSettings"]["matchupPeriodCount"]
    assert n_regular_weeks != NFL_REGULAR_SEASON_GAMES  # the two must not be conflated


def test_derive_availability_full_season() -> None:
    assert derive_availability(NFL_REGULAR_SEASON_GAMES) == pytest.approx(1.0)


def test_derive_availability_partial_season() -> None:
    result = derive_availability(10)
    assert result == pytest.approx(10 / NFL_REGULAR_SEASON_GAMES)
    assert 0.0 < result < 1.0


def test_derive_availability_raises_on_non_positive() -> None:
    with pytest.raises(ValueError, match="must be > 0"):
        derive_availability(0)
    with pytest.raises(ValueError, match="must be > 0"):
        derive_availability(-1)


def test_derive_availability_raises_rather_than_clips_when_over_season_length() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        derive_availability(NFL_REGULAR_SEASON_GAMES + 1)
