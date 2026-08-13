"""Position-level scoring variance, fitted from real historical data.

CLAUDE.md requires variance parameters to come from fitted historical data,
never an invented constant. ESPN's player stat blocks (both `_freeAgents`
and drafted-roster entries -- see `ingest.parse.every_player_with_stats`)
only expose season-aggregate stat blocks per player (a single
projected-season-total block and a single prior-actual-season-total block;
see `ingest/parse.py::_find_season_projection` for the sibling projection
lookup), regardless of whether the league has drafted. There is no per-week
(per `scoringPeriodId`) game log anywhere in this fixture format, so a
genuine per-player week-to-week variance cannot be fitted from it directly.

What this module fits instead: a **position-level coefficient of variation**
(CV = population sd / mean) from the real cross-sectional dispersion of every
usable player's *actual* prior-season per-game scoring rate at that position
(`statSourceId=0` "actual", `statSplitTypeId=0` "season total",
`seasonId=<fixture's own seasonId> - 1`, using ESPN's own `appliedAverage`).
This is real, computed data pulled straight from the fixture -- not a
borrowed industry figure, not a hand-picked plausible number.

Explicit stated assumption (documented per CLAUDE.md, which allows "fitted
from data" OR "explicit assumption with rationale" -- this is a considered
hybrid of both): cross-player dispersion of season-long per-game averages at
a position is used as a *proxy* for a single player's week-to-week
dispersion. These are not the same statistical quantity. Cross-player
dispersion mixes genuine game-to-game randomness with differences in talent,
opportunity and role between players; a single player's week-to-week
dispersion is driven by matchup variance, game script and workload swings
around their own established role. In the absence of a weekly game log in
this fixture, cross-player dispersion at a position is the best data-grounded
proxy available, and it points the right direction (RB/WR/D-ST scoring is
much spikier than QB or K scoring, which matches common fantasy-football
experience). If ingest ever gains access to real weekly boxscores (a season
with per-`scoringPeriodId` actuals), this module should be replaced with a
genuine per-player week-to-week fit -- this is a placeholder for that, not a
final answer, and says so out loud rather than pretending otherwise.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping
from typing import Any

from sim.params.errors import InsufficientHistoricalDataError

# Below this many real samples at a position, a sample standard deviation is
# too noisy to trust as a model parameter that feeds every simulated season.
# This is a data-quality guardrail, not a modelling assumption -- every
# position actually present in this fixture clears it by a wide margin (see
# sim/tests/test_params_variance.py), so it never silently changes behavior
# on the fixture this repo ships with.
_MIN_SAMPLE_SIZE = 10

# ESPN's own stat-block identifiers for a season-aggregate *actual* line (as
# opposed to a projection, and as opposed to a single scoring period).
_ACTUAL_SOURCE_ID = 0
_SEASON_SPLIT_TYPE_ID = 0


def fit_position_cv(
    raw: Mapping[str, Any], min_sample_size: int = _MIN_SAMPLE_SIZE
) -> dict[int, float]:
    """Fit a coefficient of variation per `defaultPositionId` from real
    prior-season actual scoring rates in `raw['_freeAgents']`.

    The historical season is `raw['seasonId'] - 1` -- the most recently
    completed season relative to the fixture's own season, derived rather
    than hardcoded so this does not silently point at the wrong year if
    re-run against a fixture from a different season.

    Raises InsufficientHistoricalDataError for any position with fewer than
    `min_sample_size` usable historical samples, rather than fitting (and
    silently trusting) a noisy estimate from too few players.
    """
    free_agents = raw.get("_freeAgents")
    if not isinstance(free_agents, list) or not free_agents:
        raise ValueError("fixture has no '_freeAgents' list")

    historical_season_id = raw["seasonId"] - 1

    rates_by_position: dict[int, list[float]] = {}
    for entry in free_agents:
        player = entry["player"]
        position_id = player.get("defaultPositionId")
        # Register every position seen, even with zero valid samples, so a
        # position that ends up under min_sample_size still raises below
        # instead of silently vanishing from the returned dict.
        rates_by_position.setdefault(position_id, [])
        for block in player.get("stats", []):
            if (
                block.get("statSourceId") == _ACTUAL_SOURCE_ID
                and block.get("statSplitTypeId") == _SEASON_SPLIT_TYPE_ID
                and block.get("seasonId") == historical_season_id
                and block.get("stats")
            ):
                applied_average = block.get("appliedAverage")
                if applied_average and applied_average > 0:
                    rates_by_position[position_id].append(float(applied_average))
                break

    cv_by_position: dict[int, float] = {}
    for position_id, rates in rates_by_position.items():
        if len(rates) < min_sample_size:
            raise InsufficientHistoricalDataError(
                f"only {len(rates)} historical samples for defaultPositionId="
                f"{position_id} in season {historical_season_id} (need >= "
                f"{min_sample_size}) -- refusing to fit a coefficient of "
                "variation from too small a sample"
            )
        mean = statistics.mean(rates)
        # Sample standard deviation (Bessel's correction): these players are
        # a sample of the position's population, not the whole population.
        sd = statistics.stdev(rates)
        if mean <= 0:
            raise InsufficientHistoricalDataError(
                f"non-positive mean historical scoring rate for "
                f"defaultPositionId={position_id}: {mean}"
            )
        cv_by_position[position_id] = sd / mean

    return cv_by_position
