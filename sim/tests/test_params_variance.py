"""Tests for sim.params.variance against the real saved fixture."""

from __future__ import annotations

from typing import Any

import pytest

from ingest.parse import FIXTURES_DIR, load_fixture
from ingest.slots import DEFAULT_POSITION_TO_SLOT
from sim.params.errors import InsufficientHistoricalDataError
from sim.params.variance import fit_position_cv

FIXTURE_PATH = FIXTURES_DIR / "league_raw_2026.json"


@pytest.fixture(scope="module")
def raw() -> dict[str, Any]:
    return load_fixture(FIXTURE_PATH)


def test_fit_position_cv_covers_every_position_in_the_pool(raw: dict[str, Any]) -> None:
    cv = fit_position_cv(raw)
    assert set(cv) == set(DEFAULT_POSITION_TO_SLOT)


def test_fit_position_cv_values_are_positive_and_plausible(raw: dict[str, Any]) -> None:
    """Real fantasy scoring intuition: RB/WR/D-ST are spikier (higher CV)
    than QB/K. Not a hardcoded assertion of exact values -- a directional
    sanity check that the fit isn't nonsense."""
    cv = fit_position_cv(raw)
    for position_id, value in cv.items():
        assert value > 0, f"position {position_id} has non-positive cv"

    qb, rb, wr, k = cv[1], cv[2], cv[3], cv[5]
    assert rb > qb
    assert wr > qb
    assert rb > k
    assert wr > k


def test_fit_position_cv_uses_prior_season(raw: dict[str, Any]) -> None:
    """historical_season_id must be raw['seasonId'] - 1, not hardcoded --
    regression guard against silently pointing at the wrong year."""
    wrong_season_raw = dict(raw)
    wrong_season_raw["seasonId"] = raw["seasonId"] + 50  # no data exists for this "prior" year
    with pytest.raises(InsufficientHistoricalDataError):
        fit_position_cv(wrong_season_raw, min_sample_size=1)


def test_fit_position_cv_raises_on_small_sample() -> None:
    synthetic_raw = {
        "seasonId": 2026,
        "_freeAgents": [
            {
                "player": {
                    "id": i,
                    "defaultPositionId": 1,
                    "stats": [
                        {
                            "statSourceId": 0,
                            "statSplitTypeId": 0,
                            "seasonId": 2025,
                            "appliedAverage": 10.0 + i,
                            "stats": {"0": 1.0},
                        }
                    ],
                }
            }
            for i in range(3)
        ],
    }
    with pytest.raises(InsufficientHistoricalDataError, match="only 3 historical samples"):
        fit_position_cv(synthetic_raw, min_sample_size=10)


def test_fit_position_cv_ignores_zero_or_missing_applied_average() -> None:
    synthetic_raw = {
        "seasonId": 2026,
        "_freeAgents": [
            {
                "player": {
                    "id": i,
                    "defaultPositionId": 1,
                    "stats": [
                        {
                            "statSourceId": 0,
                            "statSplitTypeId": 0,
                            "seasonId": 2025,
                            "appliedAverage": 0.0,
                            "stats": {"0": 1.0},
                        }
                    ],
                }
            }
            for i in range(20)
        ],
    }
    with pytest.raises(InsufficientHistoricalDataError, match="only 0 historical samples"):
        fit_position_cv(synthetic_raw, min_sample_size=1)
