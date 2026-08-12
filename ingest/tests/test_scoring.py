"""Tests for ingest.scoring.

The real-fixture tests are the important ones: they prove `score_stats`
reproduces ESPN's own `appliedTotal` for real players at every position
present in the league, computed purely from this league's own
scoringSettings -- not a hardcoded PPR or standard assumption.
"""

from __future__ import annotations

from typing import Any

import pytest

from ingest.parse import FIXTURES_DIR, load_fixture
from ingest.scoring import ScoringTable, build_scoring_table, score_stats
from ingest.slots import DEFAULT_POSITION_TO_SLOT

FIXTURE_PATH = FIXTURES_DIR / "league_raw_2026.json"


@pytest.fixture(scope="module")
def raw() -> dict[str, Any]:
    return load_fixture(FIXTURE_PATH)


def test_build_scoring_table_requires_scoring_items() -> None:
    with pytest.raises(ValueError, match="scoringItems"):
        build_scoring_table({})


def test_score_stats_applies_base_rate() -> None:
    table = ScoringTable(rate={"53": 1.0})  # 1 pt / reception, full PPR
    assert score_stats({"53": 6.0}, table, lineup_slot_id=4) == pytest.approx(6.0)


def test_score_stats_ignores_unrated_categories() -> None:
    table = ScoringTable(rate={"53": 1.0})
    # statId "999" has no scoring rule -- ESPN's list is exhaustive over
    # categories that score, so this is correctly worth zero, not an error.
    assert score_stats({"53": 3.0, "999": 42.0}, table, lineup_slot_id=4) == pytest.approx(3.0)


def test_score_stats_reverse_item_is_negative() -> None:
    table = build_scoring_table(
        {"scoringItems": [{"statId": 20, "points": 2.0, "isReverseItem": True, "pointsOverrides": {}}]}
    )
    assert score_stats({"20": 3.0}, table, lineup_slot_id=0) == pytest.approx(-6.0)


def test_score_stats_applies_slot_override() -> None:
    table = build_scoring_table(
        {
            "scoringItems": [
                {
                    "statId": 89,
                    "points": 0.0,
                    "isReverseItem": False,
                    "pointsOverrides": {"16": 5.0},
                }
            ]
        }
    )
    # Scored as D/ST (slot 16): override applies.
    assert score_stats({"89": 1.0}, table, lineup_slot_id=16) == pytest.approx(5.0)
    # Scored as any other slot: falls back to the base rate (0.0 here).
    assert score_stats({"89": 1.0}, table, lineup_slot_id=4) == pytest.approx(0.0)


@pytest.mark.parametrize("default_position_id", [1, 2, 3, 4, 5, 16])
def test_reproduces_espn_applied_total_for_every_position(
    raw: dict[str, Any], default_position_id: int
) -> None:
    """For a real player at each position present in the fixture, our own
    scoring computation over raw stats must match ESPN's own appliedTotal
    for that player's season-projection stat block -- proof this league's
    actual (fully custom) scoringSettings are being used, not assumed."""
    table = build_scoring_table(raw["settings"]["scoringSettings"])
    season_id = raw["seasonId"]
    slot_id = DEFAULT_POSITION_TO_SLOT[default_position_id]

    sample = None
    for entry in raw["_freeAgents"]:
        player = entry["player"]
        if player["defaultPositionId"] != default_position_id:
            continue
        for block in player["stats"]:
            if (
                block["statSourceId"] == 1
                and block["statSplitTypeId"] == 0
                and block["seasonId"] == season_id
                and block.get("appliedTotal")
            ):
                sample = block
                break
        if sample is not None:
            break

    assert sample is not None, f"fixture has no scored sample for position {default_position_id}"

    computed = score_stats(sample["stats"], table, lineup_slot_id=slot_id)
    assert computed == pytest.approx(sample["appliedTotal"], abs=1e-5)
