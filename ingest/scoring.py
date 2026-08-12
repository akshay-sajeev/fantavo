"""Compute fantasy points from raw ESPN stat lines using a league's own
scoringSettings.

CLAUDE.md is explicit: league scoring is fully custom and must never be
assumed to be standard or PPR. This module builds a scoring table from
`settings.scoringSettings.scoringItems` and applies it to a player's raw
per-stat-category values -- it never hardcodes a scoring rule.

Verified against fixtures/league_raw_2026.json: for a sample player at every
position present in the fixture, `score_stats` reproduces ESPN's own
`appliedTotal` for the season-projection stat block to within floating-point
rounding. See ingest/tests/test_scoring.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScoringTable:
    """Points-per-unit for each ESPN statId, with optional per-slot overrides.

    `rate[stat_id]` is the base points-per-unit for that stat category, with
    `isReverseItem` already folded in as a sign flip. `overrides[stat_id]`
    maps a lineupSlotId (as a string, matching ESPN's JSON) to a replacement
    rate for that stat when scored in that slot -- used by this league for
    D/ST-specific stat categories that share a statId with offensive
    categories.
    """

    rate: dict[str, float] = field(default_factory=dict)
    overrides: dict[str, dict[str, float]] = field(default_factory=dict)


def build_scoring_table(scoring_settings: Mapping[str, object]) -> ScoringTable:
    """Build a ScoringTable from a league's raw `settings.scoringSettings`.

    Raises if `scoringItems` is missing -- there is no reasonable default
    scoring table to fall back to.
    """
    items = scoring_settings.get("scoringItems")
    if not isinstance(items, list):
        raise ValueError(
            "scoringSettings.scoringItems is missing or not a list -- cannot "
            "compute this league's scoring without it."
        )

    rate: dict[str, float] = {}
    overrides: dict[str, dict[str, float]] = {}
    for item in items:
        stat_id = str(item["statId"])
        sign = -1.0 if item.get("isReverseItem") else 1.0
        rate[stat_id] = float(item["points"]) * sign
        raw_overrides = item.get("pointsOverrides") or {}
        if raw_overrides:
            overrides[stat_id] = {
                slot_key: float(value) * sign for slot_key, value in raw_overrides.items()
            }

    return ScoringTable(rate=rate, overrides=overrides)


def score_stats(
    stats: Mapping[str, float], table: ScoringTable, lineup_slot_id: int
) -> float:
    """Total fantasy points for one raw stat line, scored as if started in
    `lineup_slot_id`.

    Stat categories with no entry in the scoring table (e.g. yardage
    breakdowns the league doesn't award points for) contribute zero, which
    is correct -- ESPN's own scoringItems list is exhaustive over categories
    that *do* score, so an absent statId genuinely means "not scored," not
    "missing data."
    """
    slot_key = str(lineup_slot_id)
    total = 0.0
    for stat_id, value in stats.items():
        slot_overrides = table.overrides.get(stat_id)
        rate = slot_overrides.get(slot_key) if slot_overrides else None
        if rate is None:
            rate = table.rate.get(stat_id)
        if rate is None:
            continue
        total += rate * value
    return total
