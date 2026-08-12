from __future__ import annotations

from ingest.slots import NON_STARTING_SLOTS, starting_slot_counts


def test_starting_slot_counts_excludes_bench_and_ir() -> None:
    # Shape matches fixtures/league_raw_2026.json's rosterSettings.lineupSlotCounts.
    lineup_slot_counts = {
        "0": 1,
        "2": 2,
        "4": 2,
        "6": 1,
        "16": 1,
        "17": 1,
        "20": 7,
        "21": 1,
        "23": 1,
        "3": 0,
        "5": 0,
    }
    result = starting_slot_counts(lineup_slot_counts)
    assert result == {0: 1, 2: 2, 4: 2, 6: 1, 16: 1, 17: 1, 23: 1}
    assert set(result) & NON_STARTING_SLOTS == set()


def test_starting_slot_counts_drops_zero_counts() -> None:
    result = starting_slot_counts({"0": 1, "7": 0})
    assert result == {0: 1}
