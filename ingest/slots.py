"""ESPN lineupSlotId / defaultPositionId constants.

Two *different* ESPN enumerations are involved here and they must not be
conflated (see CLAUDE.md's domain glossary):

- ``lineupSlotId`` -- where a player sits in a roster (QB, RB, ..., Bench,
  IR, Flex). This numbering is fixed by ESPN's API, but which slots a league
  actually *uses*, and how many of each, is league-specific and lives in
  ``settings.rosterSettings.lineupSlotCounts``. Never assume a league's slot
  composition -- read it from there.
- ``defaultPositionId`` -- a player's own listed position. This is a
  *separate* numbering from lineupSlotId (e.g. WR is 4 as a lineup slot but
  3 as a default position). scoringSettings.scoringItems.pointsOverrides is
  keyed by lineupSlotId, so a defaultPositionId must be mapped to its
  corresponding *starting* lineupSlotId before it can be used to look up an
  override -- get this mapping wrong and D/ST- or K-specific scoring rules
  silently apply to the wrong stat lines.

The ID -> name mapping below is ESPN's own protocol enum (stable across
leagues), not a per-league setting. The defaultPositionId -> lineupSlotId
mapping was verified against fixtures/league_raw_2026.json by cross-checking
that scoring computed with each mapped slot reproduces ESPN's own
appliedTotal for a sample player at every position present in the fixture
(QB, RB, WR, TE, K, D/ST) -- see ingest/tests/test_scoring.py.
"""

from __future__ import annotations

# lineupSlotId -> canonical name. Bench/IR/Flex are structural, not
# positions; everything else is a starting position slot.
SLOT_NAMES: dict[int, str] = {
    0: "QB",
    1: "TQB",
    2: "RB",
    3: "RB/WR",
    4: "WR",
    5: "WR/TE",
    6: "TE",
    7: "OP",
    8: "DT",
    9: "DE",
    10: "LB",
    11: "DL",
    12: "CB",
    13: "S",
    14: "DB",
    15: "DP",
    16: "D/ST",
    17: "K",
    18: "P",
    19: "HC",
    20: "BE",
    21: "IR",
    23: "FLEX",
    24: "ER",
}

BENCH_SLOT_ID = 20
IR_SLOT_ID = 21
NON_STARTING_SLOTS = frozenset({BENCH_SLOT_ID, IR_SLOT_ID})

# defaultPositionId -> canonical name (ESPN protocol enum).
DEFAULT_POSITION_NAMES: dict[int, str] = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "D/ST",
}

# defaultPositionId -> the lineupSlotId used to look up scoringItems
# pointsOverrides for that position. Verified against this league's fixture
# (see module docstring); not the same numbering as defaultPositionId.
DEFAULT_POSITION_TO_SLOT: dict[int, int] = {
    1: 0,  # QB
    2: 2,  # RB
    3: 4,  # WR
    4: 6,  # TE
    5: 17,  # K
    16: 16,  # D/ST
}


def starting_slot_counts(lineup_slot_counts: dict[str, int]) -> dict[int, int]:
    """A league's starting-lineup composition, derived from its own settings.

    `lineup_slot_counts` is `settings.rosterSettings.lineupSlotCounts` as-is
    (string keys, per ESPN's JSON). Bench and IR slots are excluded -- they
    are not part of the starting lineup. Slots with a zero count are
    excluded too, since the league does not use them.
    """
    result: dict[int, int] = {}
    for key, count in lineup_slot_counts.items():
        slot_id = int(key)
        if slot_id in NON_STARTING_SLOTS or count <= 0:
            continue
        result[slot_id] = count
    return result
