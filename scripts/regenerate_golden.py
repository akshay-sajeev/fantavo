#!/usr/bin/env python3
"""Print current golden values for sim/tests/test_engine.py.

Run this ONLY when you have deliberately changed the model and understand why the
numbers moved. Paste the output into the golden tests and note the reason in
docs/decisions.md. If you find yourself running this to make a red test go green,
stop -- the test is telling you something.

    python scripts/regenerate_golden.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sim.engine import simulate_seasons  # noqa: E402
from sim.tests.test_engine import (  # noqa: E402
    GOLDEN_SEED,
    GOLDEN_SIMS,
    _STRENGTHS,
    _make_team,
)
from sim.engine import LeagueParams, round_robin_schedule  # noqa: E402


def _fmt(name: str, values: np.ndarray, places: int) -> str:
    rows = [
        "        " + ", ".join(f"{v:.{places}f}" for v in values[i : i + 5]) + ","
        for i in range(0, len(values), 5)
    ]
    return f"    # {name}\n    expected = [\n" + "\n".join(rows) + "\n    ]"


def main() -> None:
    league = LeagueParams(
        teams=tuple(_make_team(i, s) for i, s in enumerate(_STRENGTHS)),
        schedule=round_robin_schedule(n_teams=10, n_weeks=14),
        n_playoff_teams=6,
    )
    result = simulate_seasons(
        league, n_sims=GOLDEN_SIMS, rng=np.random.default_rng(GOLDEN_SEED)
    )

    print(f"# seed={GOLDEN_SEED} n_sims={GOLDEN_SIMS}\n")
    print(_fmt("won_title", result.won_title, 5), "\n")
    print(_fmt("made_playoffs", result.made_playoffs, 5), "\n")
    print(_fmt("mean_wins", result.mean_wins, 5))


if __name__ == "__main__":
    main()