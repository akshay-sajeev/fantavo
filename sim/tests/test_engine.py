"""Tests for the season simulator.

The golden tests are the important ones. They pin exact output for a fixed seed
so that a refactor which silently changes your probabilities fails loudly instead
of quietly shipping different numbers. If a golden test fails, do NOT update the
expected value until you understand why it moved -- that is the whole point.
"""

from __future__ import annotations

import numpy as np
import pytest

from sim.engine import (
    LeagueParams,
    PlayerParams,
    SimulationResult,
    TeamParams,
    round_robin_schedule,
    simulate_seasons,
)

GOLDEN_SEED = 20260812
GOLDEN_SIMS = 4_000

# Strength multiplier per team: team 0 is strongest, team 9 weakest. Fixed so the
# golden numbers stay meaningful and the ordering assertions have real content.
#
# The spread is deliberately narrow. Real rosters differ by only a few percent in
# expected weekly points, and weekly variance dominates -- a 10-team league where
# the best roster wins 40% of titles is a modelling error, not a strong team.
_STRENGTHS = (1.070, 1.050, 1.032, 1.016, 1.000, 0.988, 0.974, 0.958, 0.940, 0.920)


def _make_team(team_id: int, strength: float) -> TeamParams:
    """Nine starters with a plausible fantasy scoring spread."""
    base = [
        (18.0, 6.5, 0.97),  # QB
        (14.0, 7.0, 0.90),  # RB1
        (11.0, 6.0, 0.90),  # RB2
        (14.5, 7.5, 0.93),  # WR1
        (11.5, 6.5, 0.93),  # WR2
        (9.0, 5.5, 0.93),  # WR3
        (9.5, 5.5, 0.94),  # TE
        (8.0, 5.0, 0.99),  # K
        (7.5, 4.5, 1.00),  # D/ST
    ]
    return TeamParams(
        team_id=team_id,
        name=f"Team {team_id}",
        starters=tuple(
            PlayerParams(
                player_id=team_id * 100 + i,
                mean=mean * strength,
                sd=sd,
                availability=avail,
            )
            for i, (mean, sd, avail) in enumerate(base)
        ),
    )


@pytest.fixture
def league() -> LeagueParams:
    return LeagueParams(
        teams=tuple(_make_team(i, s) for i, s in enumerate(_STRENGTHS)),
        schedule=round_robin_schedule(n_teams=10, n_weeks=14),
        n_playoff_teams=6,
    )


@pytest.fixture
def result(league: LeagueParams) -> SimulationResult:
    return simulate_seasons(
        league, n_sims=GOLDEN_SIMS, rng=np.random.default_rng(GOLDEN_SEED)
    )


# --------------------------------------------------------------------------
# Golden tests -- exact values, fixed seed
# --------------------------------------------------------------------------


def test_golden_title_probabilities(result: SimulationResult) -> None:
    """Regenerate with scripts/regenerate_golden.py ONLY after understanding
    why the numbers changed."""
    expected = [
        0.24050, 0.19400, 0.16000, 0.11775, 0.09200,
        0.06600, 0.05475, 0.03750, 0.02300, 0.01450,
    ]
    np.testing.assert_allclose(result.won_title, expected, atol=1e-9)


def test_golden_playoff_probabilities(result: SimulationResult) -> None:
    expected = [
        0.88200, 0.84600, 0.79000, 0.70350, 0.66875,
        0.56925, 0.52450, 0.42400, 0.34875, 0.24325,
    ]
    np.testing.assert_allclose(result.made_playoffs, expected, atol=1e-9)


def test_golden_mean_wins(result: SimulationResult) -> None:
    expected = [
        8.44875, 8.19250, 7.85800, 7.39625, 7.21575,
        6.80150, 6.62750, 6.17450, 5.89375, 5.39150,
    ]
    np.testing.assert_allclose(result.mean_wins, expected, atol=1e-9)


def test_determinism_same_seed(league: LeagueParams) -> None:
    a = simulate_seasons(league, n_sims=500, rng=np.random.default_rng(7))
    b = simulate_seasons(league, n_sims=500, rng=np.random.default_rng(7))
    np.testing.assert_array_equal(a.won_title, b.won_title)
    np.testing.assert_array_equal(a.wins, b.wins)


def test_different_seeds_differ(league: LeagueParams) -> None:
    a = simulate_seasons(league, n_sims=500, rng=np.random.default_rng(7))
    b = simulate_seasons(league, n_sims=500, rng=np.random.default_rng(8))
    assert not np.array_equal(a.wins, b.wins)


# --------------------------------------------------------------------------
# Invariants -- these must hold for any league, any seed
# --------------------------------------------------------------------------


def test_probabilities_sum_to_one(result: SimulationResult) -> None:
    assert result.won_title.sum() == pytest.approx(1.0)
    assert result.reached_final.sum() == pytest.approx(2.0)
    np.testing.assert_allclose(result.finish_distribution.sum(axis=1), 1.0)


def test_playoff_field_size_is_exact(result: SimulationResult, league: LeagueParams) -> None:
    assert result.made_playoffs.sum() == pytest.approx(float(league.n_playoff_teams))


def test_every_team_plays_every_week(result: SimulationResult, league: LeagueParams) -> None:
    total = result.wins.sum(axis=1)
    games_per_week = league.n_teams // 2
    assert np.all(total <= league.n_regular_weeks * games_per_week)


def test_scores_are_non_negative(result: SimulationResult) -> None:
    """Gamma sampling exists precisely to guarantee this. A normal distribution
    would produce negative weeks for low-mean players."""
    assert np.all(result.points_for >= 0)


def test_stronger_teams_win_more(result: SimulationResult) -> None:
    """Monotonicity: title odds should decrease as roster strength decreases."""
    assert np.all(np.diff(result.won_title) < 0)
    assert np.all(np.diff(result.made_playoffs) < 0)


def test_title_prob_below_playoff_prob(result: SimulationResult) -> None:
    assert np.all(result.won_title <= result.made_playoffs + 1e-12)
    assert np.all(result.reached_final <= result.made_playoffs + 1e-12)
    assert np.all(result.won_title <= result.reached_final + 1e-12)


def test_power_ranking_is_derived_not_scored(result: SimulationResult) -> None:
    ranking = result.power_ranking()
    assert [name for name, _, _ in ranking] == [f"Team {i}" for i in range(10)]
    assert ranking[0][1] >= ranking[-1][1]


# --------------------------------------------------------------------------
# What-if / trade path -- same engine, different inputs
# --------------------------------------------------------------------------


def test_roster_override_improves_odds(league: LeagueParams) -> None:
    """A trade evaluation is just simulate_seasons() with overridden starters."""
    weakest = league.teams[-1]
    upgraded = tuple(
        PlayerParams(p.player_id, p.mean * 1.6, p.sd, p.availability) for p in weakest.starters
    )

    before = simulate_seasons(league, n_sims=2_000, rng=np.random.default_rng(11))
    after = simulate_seasons(
        league,
        n_sims=2_000,
        rng=np.random.default_rng(11),
        roster_overrides={weakest.team_id: upgraded},
    )

    assert after.won_title[-1] > before.won_title[-1]
    assert after.made_playoffs[-1] > before.made_playoffs[-1]


def test_roster_override_does_not_mutate_league(league: LeagueParams) -> None:
    original = league.teams[0].starters
    simulate_seasons(
        league,
        n_sims=100,
        rng=np.random.default_rng(3),
        roster_overrides={league.teams[0].team_id: original[:5]},
    )
    assert league.teams[0].starters == original


def test_unknown_team_id_in_override_raises(league: LeagueParams) -> None:
    with pytest.raises(ValueError, match="unknown team ids"):
        simulate_seasons(
            league, n_sims=10, rng=np.random.default_rng(1), roster_overrides={999: ()}
        )


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"mean": 0.0, "sd": 5.0, "availability": 1.0}, "mean must be"),
        ({"mean": 10.0, "sd": 0.0, "availability": 1.0}, "sd must be"),
        ({"mean": 10.0, "sd": 5.0, "availability": 1.5}, "availability must be"),
    ],
)
def test_invalid_player_params_raise(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        PlayerParams(player_id=1, **kwargs)


def test_asymmetric_schedule_raises() -> None:
    bad = np.array([[1, 2, 0, 3]], dtype=np.int64)
    with pytest.raises(ValueError, match="not a valid symmetric pairing"):
        LeagueParams(
            teams=tuple(_make_team(i, 1.0) for i in range(4)),
            schedule=bad,
            n_playoff_teams=2,
        )


def test_team_playing_itself_raises() -> None:
    bad = np.array([[0, 1, 3, 2]], dtype=np.int64)
    with pytest.raises(ValueError, match="playing itself"):
        LeagueParams(
            teams=tuple(_make_team(i, 1.0) for i in range(4)),
            schedule=bad,
            n_playoff_teams=2,
        )


def test_round_robin_is_symmetric() -> None:
    schedule = round_robin_schedule(n_teams=12, n_weeks=13)
    for week in schedule:
        np.testing.assert_array_equal(week[week], np.arange(12))
        assert not np.any(week == np.arange(12))


@pytest.mark.parametrize("n_playoff_teams", [2, 4, 6, 8])
def test_bracket_sizes(league: LeagueParams, n_playoff_teams: int) -> None:
    variant = LeagueParams(
        teams=league.teams, schedule=league.schedule, n_playoff_teams=n_playoff_teams
    )
    res = simulate_seasons(variant, n_sims=500, rng=np.random.default_rng(5))
    assert res.won_title.sum() == pytest.approx(1.0)
    assert res.made_playoffs.sum() == pytest.approx(float(n_playoff_teams))