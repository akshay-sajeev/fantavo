"""SYNTHETIC DATA VALIDATION -- prints simulated results for a mock league so
a human can sanity check sim/params against real fantasy-scoring intuition.

This script does NOT predict anything about the user's real league. It runs
fixture -> sim.params -> sim.engine.simulate_seasons() over a mock league
built by sim.params.mock_rosters (real players, fake team groupings -- see
that module's docstring for why). Every printed team name says "SYNTHETIC" so
this output can never be mistaken for a real forecast.

Usage:
    python -m sim.params.validate [path/to/fixture.json]
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from ingest.parse import FIXTURES_DIR, load_fixture, parse_player_pool, parse_scoring_table
from sim.engine import LeagueParams, SimulationResult, _sample_team_weeks, simulate_seasons
from sim.params.derive import derive_player_params_pool
from sim.params.mock_rosters import build_mock_league
from sim.params.variance import fit_position_cv

# Fixed, documented, mandatory per CLAUDE.md's "seeds are mandatory" rule --
# no module-level RNG is used anywhere else in this file.
VALIDATION_SEED = 20260212
VALIDATION_SIMS = 10_000

# PLAN.md's Phase 2 sanity check: the strongest roster in a 10-team league
# should land roughly 15-25% title odds. Above this, variance is too low --
# a modelling bug, not a strong team.
_TITLE_ODDS_WARN_THRESHOLD = 0.35
_TITLE_ODDS_TARGET_LOW = 0.15
_TITLE_ODDS_TARGET_HIGH = 0.25


def _print_weekly_score_distributions(league: LeagueParams, rng: np.random.Generator) -> None:
    """Print mean/sd/percentiles of simulated weekly team scores.

    Reuses sim.engine._sample_team_weeks directly -- the exact same sampling
    code simulate_seasons() calls internally -- rather than reimplementing
    weekly sampling here, per CLAUDE.md's "one simulation engine" rule.
    SimulationResult only exposes season totals, not per-week scores, so
    this is the only way to surface weekly distributions without a second
    sampling path.
    """
    print("  SYNTHETIC weekly score distributions (per simulated matchup):")
    print(f"  {'team':<45} {'mean':>7} {'sd':>7} {'p10':>7} {'p50':>7} {'p90':>7}")
    for team in league.teams:
        weekly = _sample_team_weeks(team, VALIDATION_SIMS, league.n_regular_weeks, rng)
        flat = weekly.ravel()
        p10, p50, p90 = np.percentile(flat, [10, 50, 90])
        print(
            f"  {team.name:<45} {flat.mean():7.1f} {flat.std():7.1f} "
            f"{p10:7.1f} {p50:7.1f} {p90:7.1f}"
        )


def _print_title_odds(result: SimulationResult) -> float:
    print()
    print("  SYNTHETIC title / playoff odds (mock league, NOT a real forecast):")
    print(f"  {'team':<45} {'title%':>8} {'playoff%':>9}")
    ranking = result.power_ranking()
    for name, title_prob, playoff_prob in ranking:
        print(f"  {name:<45} {title_prob * 100:7.2f}% {playoff_prob * 100:8.2f}%")
    return ranking[0][1]


def _print_sanity_check(strongest_title_odds: float, n_teams: int) -> None:
    print()
    pct = strongest_title_odds * 100
    if strongest_title_odds > _TITLE_ODDS_WARN_THRESHOLD:
        print(
            f"  WARNING: strongest mock roster's title odds ({pct:.1f}%) exceed "
            f"{_TITLE_ODDS_WARN_THRESHOLD * 100:.0f}% in a {n_teams}-team league. "
            "Per PLAN.md's Phase 2 sanity check, this signals variance is too "
            "low -- a modelling bug, not a strong team. Re-check sd derivation "
            "in sim/params/variance.py before trusting this pipeline."
        )
    elif _TITLE_ODDS_TARGET_LOW <= strongest_title_odds <= _TITLE_ODDS_TARGET_HIGH:
        print(
            f"  PASS: strongest mock roster's title odds ({pct:.1f}%) fall "
            f"within PLAN.md's rough {_TITLE_ODDS_TARGET_LOW * 100:.0f}-"
            f"{_TITLE_ODDS_TARGET_HIGH * 100:.0f}% target band for a "
            f"{n_teams}-team league."
        )
    else:
        print(
            f"  OK (outside the rough target band, below the hard threshold): "
            f"strongest mock roster's title odds are {pct:.1f}%, above PLAN.md's "
            f"{_TITLE_ODDS_TARGET_HIGH * 100:.0f}% rough target but comfortably "
            f"under the {_TITLE_ODDS_WARN_THRESHOLD * 100:.0f}% bug threshold. "
            "Plausible explanation for a mock league specifically: a snake "
            "draft with an odd number of rounds gives the team picking first "
            "a small structural edge (it gets one more first-pick-of-the-round "
            "than the team picking last) -- see sim/params/mock_rosters.py's "
            "_ROUND_SEQUENCE. This is a property of the synthetic draft, not "
            "evidence of a variance bug; if a real post-draft fixture later "
            "shows the same pattern, treat it as a real signal to investigate."
        )


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURES_DIR / "league_raw_2026.json"
    raw = load_fixture(path)

    print("=" * 78)
    print("SYNTHETIC DATA -- pipeline validation only, NOT real team predictions")
    print("=" * 78)
    print(f"Source fixture: {path}")
    print(
        "This league's real teams have no drafted rosters yet (pre-draft "
        "fixture) -- see docs/decisions.md. Every team below is a fabricated "
        "mock roster assembled from this fixture's real player pool."
    )

    scoring_table = parse_scoring_table(raw)
    player_pool, skipped = parse_player_pool(raw, scoring_table)
    print(f"\nReal player pool: {len(player_pool)} usable projections, {len(skipped)} skipped")

    position_cv = fit_position_cv(raw)
    print("\nFitted position coefficients of variation (sd / mean), from real")
    print("prior-season actual scoring rates (see sim/params/variance.py):")
    for position_id, cv in sorted(position_cv.items()):
        print(f"  defaultPositionId={position_id:>3}: cv={cv:.3f}")

    params_by_id = derive_player_params_pool(player_pool, position_cv)

    rng = np.random.default_rng(VALIDATION_SEED)
    league = build_mock_league(raw, player_pool, params_by_id, n_mock_teams=10)

    print(f"\nMock league: {league.n_teams} teams, {league.n_regular_weeks} regular weeks, "
          f"{league.n_playoff_teams} playoff spots")
    print()
    _print_weekly_score_distributions(league, rng)

    result = simulate_seasons(league, n_sims=VALIDATION_SIMS, rng=rng)
    strongest_title_odds = _print_title_odds(result)
    _print_sanity_check(strongest_title_odds, league.n_teams)


if __name__ == "__main__":
    main()
