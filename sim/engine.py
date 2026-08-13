"""Monte Carlo season simulator.

This is the single source of truth for simulated season outcomes. Playoff odds,
title odds, power rankings, trade evaluation and what-if scenarios are all this
function called with different inputs. Do not write a second simulation path.

Scope note: v1 models a fixed starting lineup per team for the whole season. It
does not model waiver churn, lineup decisions, or in-season roster changes. That
is a deliberate simplification, not an oversight -- extend `_sample_team_weeks`
when you add it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

# Tiebreaker weight: wins dominate, points-for breaks ties. Season points-for is
# bounded well below this, so the composite key sorts correctly.
_WIN_WEIGHT = 1_000_000.0


@dataclass(frozen=True)
class PlayerParams:
    """Weekly scoring distribution for one player.

    mean/sd are points per *played* game. `availability` is the probability the
    player is active in a given week, covering injury, bye and inactives.
    """

    player_id: int
    mean: float
    sd: float
    availability: float = 1.0

    def __post_init__(self) -> None:
        if self.mean <= 0:
            raise ValueError(f"player {self.player_id}: mean must be > 0, got {self.mean}")
        if self.sd <= 0:
            raise ValueError(f"player {self.player_id}: sd must be > 0, got {self.sd}")
        if not 0.0 <= self.availability <= 1.0:
            raise ValueError(
                f"player {self.player_id}: availability must be in [0, 1], "
                f"got {self.availability}"
            )


@dataclass(frozen=True)
class TeamParams:
    """A fantasy team's starting lineup."""

    team_id: int
    name: str
    starters: tuple[PlayerParams, ...]

    def __post_init__(self) -> None:
        if not self.starters:
            raise ValueError(f"team {self.team_id} ({self.name}) has no starters")


@dataclass(frozen=True)
class LeagueParams:
    """Everything the simulator needs about league structure.

    `schedule` has shape (n_regular_weeks, n_teams); schedule[w, t] is the team
    index t plays in week w. It must be symmetric: schedule[w, schedule[w, t]] == t.
    """

    teams: tuple[TeamParams, ...]
    schedule: np.ndarray
    n_playoff_teams: int = 6

    def __post_init__(self) -> None:
        n_teams = len(self.teams)
        if n_teams < 2:
            raise ValueError("league needs at least 2 teams")
        if self.schedule.shape[1] != n_teams:
            raise ValueError(
                f"schedule has {self.schedule.shape[1]} columns but league has {n_teams} teams"
            )
        if not 2 <= self.n_playoff_teams <= n_teams:
            raise ValueError(
                f"n_playoff_teams must be in [2, {n_teams}], got {self.n_playoff_teams}"
            )
        for w in range(self.schedule.shape[0]):
            row = self.schedule[w]
            if not np.array_equal(row[row], np.arange(n_teams)):
                raise ValueError(f"schedule week {w} is not a valid symmetric pairing")
            if np.any(row == np.arange(n_teams)):
                raise ValueError(f"schedule week {w} has a team playing itself")

    @property
    def n_teams(self) -> int:
        return len(self.teams)

    @property
    def n_regular_weeks(self) -> int:
        return int(self.schedule.shape[0])


@dataclass
class SimulationResult:
    """Outcome distribution over simulated seasons.

    Arrays are indexed by team, matching LeagueParams.teams order. Probability
    arrays are in [0, 1]. `finish_distribution[t, p]` is the probability team t
    finishes in place p (0-indexed, so column 0 is the championship).
    """

    team_ids: tuple[int, ...]
    team_names: tuple[str, ...]
    n_sims: int
    wins: np.ndarray  # (n_sims, n_teams)
    points_for: np.ndarray  # (n_sims, n_teams)
    seed_rank: np.ndarray  # (n_sims, n_teams), 0 = top seed
    made_playoffs: np.ndarray  # (n_teams,)
    won_title: np.ndarray  # (n_teams,)
    reached_final: np.ndarray  # (n_teams,)
    finish_distribution: np.ndarray  # (n_teams, n_playoff_teams + 1)
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def mean_wins(self) -> np.ndarray:
        return self.wins.mean(axis=0)

    @property
    def mean_points_for(self) -> np.ndarray:
        return self.points_for.mean(axis=0)

    def power_ranking(self) -> list[tuple[str, float, float]]:
        """Teams ordered by title probability -- the ranking is *derived*, not
        a hand-weighted score. Returns (name, title_prob, playoff_prob)."""
        order = np.argsort(-self.won_title, kind="stable")
        return [
            (self.team_names[i], float(self.won_title[i]), float(self.made_playoffs[i]))
            for i in order
        ]


def _gamma_shape_scale(mean: np.ndarray, sd: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Gamma parameters matching a target mean and sd.

    Gamma is used rather than normal because fantasy scores are right-skewed and
    floored near zero. A normal draw produces negative scores for low-mean players,
    which silently biases everything downstream.
    """
    shape = (mean / sd) ** 2
    scale = sd**2 / mean
    return shape, scale


def _sample_player_weeks(
    players: tuple[PlayerParams, ...], n_sims: int, n_weeks: int, rng: np.random.Generator
) -> npt.NDArray[np.float64]:
    """Per-player weekly scores for an arbitrary group of players. Returns
    (n_sims, n_weeks, len(players)) -- the same per-player Gamma(mean, sd) x
    availability draw `_sample_team_weeks` sums over, exposed here unsummed.

    Factored out of `_sample_team_weeks` (which now just calls this and sums)
    so a caller that needs individual player scores -- not just a team total
    -- can reuse the identical sampling primitives instead of duplicating
    them. E.g. `sim.api.season_replay_view`'s alternate-lineup what-if, which
    must compare an actual starter's realized weekly score against a bench
    player's to determine that week's optimal lineup; this is the same
    engine sampling, applied to a roster slice (starters + bench) instead of
    only a team's fixed starting lineup, not a second simulation path.

    `players` need not be a real team's `starters` tuple -- the function
    only reads `mean`/`sd`/`availability` off each `PlayerParams`, so it
    accepts any ordered group. Refactoring `_sample_team_weeks` to call this
    makes no change to that function's rng consumption (same two calls, same
    order), so golden test values are unaffected.
    """
    means = np.array([p.mean for p in players], dtype=np.float64)
    sds = np.array([p.sd for p in players], dtype=np.float64)
    avail = np.array([p.availability for p in players], dtype=np.float64)

    shape, scale = _gamma_shape_scale(means, sds)
    size = (n_sims, n_weeks, len(players))

    scores = rng.gamma(shape=shape, scale=scale, size=size)
    active = rng.random(size) < avail
    return scores * active


def _sample_team_weeks(
    team: TeamParams, n_sims: int, n_weeks: int, rng: np.random.Generator
) -> np.ndarray:
    """Weekly scores for one team. Returns (n_sims, n_weeks).

    Vectorized across sims, weeks and players simultaneously -- there is no
    per-simulation loop anywhere in this module.
    """
    return _sample_player_weeks(team.starters, n_sims, n_weeks, rng).sum(axis=2)


def _next_power_of_two(n: int) -> int:
    return 1 << (n - 1).bit_length()


def _run_playoffs(
    seeds: np.ndarray,
    playoff_scores: np.ndarray,
    seed_rank: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Single-elimination bracket with reseeding.

    `seeds` is (n_sims, k) of team indices in seed order (column 0 = top seed).
    `seed_rank` is (n_sims, n_teams), used to reseed survivors each round.
    `playoff_scores` is (n_sims, n_rounds, n_teams). Returns (champion, finalists)
    where champion is (n_sims,) and finalists is (n_sims, 2).
    """
    n_sims, k = seeds.shape
    rank_of = seed_rank

    alive = seeds
    byes = _next_power_of_two(k) - k
    round_idx = 0
    finalists = np.zeros((n_sims, 2), dtype=np.int64)

    while alive.shape[1] > 1:
        if byes:
            advancing, playing = alive[:, :byes], alive[:, byes:]
            byes = 0
        else:
            advancing, playing = alive[:, :0], alive

        m = playing.shape[1]
        high, low = playing[:, : m // 2], playing[:, m // 2 :][:, ::-1]

        week = playoff_scores[:, round_idx, :]
        high_pts = np.take_along_axis(week, high, axis=1)
        low_pts = np.take_along_axis(week, low, axis=1)
        # Coin flip on exact ties so no team gets a structural advantage.
        high_wins = np.where(
            high_pts == low_pts, rng.random(high_pts.shape) < 0.5, high_pts > low_pts
        )
        winners = np.where(high_wins, high, low)

        survivors = np.concatenate([advancing, winners], axis=1)
        # Reseed: order survivors by original seed rank.
        order = np.argsort(np.take_along_axis(rank_of, survivors, axis=1), axis=1)
        alive = np.take_along_axis(survivors, order, axis=1)

        if alive.shape[1] == 2:
            finalists = alive
        round_idx += 1

    return alive[:, 0], finalists


def simulate_seasons(
    league: LeagueParams,
    n_sims: int = 10_000,
    *,
    rng: np.random.Generator,
    roster_overrides: dict[int, tuple[PlayerParams, ...]] | None = None,
) -> SimulationResult:
    """Simulate `n_sims` complete seasons.

    `rng` is required, not optional -- results must be reproducible. Pass
    `roster_overrides` (team_id -> starters) to evaluate a trade or what-if
    without mutating the league. This is the entry point every downstream
    feature calls.
    """
    if n_sims < 1:
        raise ValueError(f"n_sims must be >= 1, got {n_sims}")

    teams = league.teams
    if roster_overrides:
        unknown = set(roster_overrides) - {t.team_id for t in teams}
        if unknown:
            raise ValueError(f"roster_overrides references unknown team ids: {sorted(unknown)}")
        teams = tuple(
            TeamParams(t.team_id, t.name, roster_overrides.get(t.team_id, t.starters))
            for t in teams
        )

    n_teams = league.n_teams
    n_weeks = league.n_regular_weeks
    n_rounds = int(np.ceil(np.log2(_next_power_of_two(league.n_playoff_teams))))

    # (n_sims, n_weeks + n_rounds, n_teams)
    scores = np.stack(
        [_sample_team_weeks(t, n_sims, n_weeks + n_rounds, rng) for t in teams], axis=2
    )
    regular, playoff_scores = scores[:, :n_weeks, :], scores[:, n_weeks:, :]

    # Head-to-head: opponent score for team t in week w.
    opp = regular[:, np.arange(n_weeks)[:, None], league.schedule]
    wins = (regular > opp).sum(axis=1)
    ties = (regular == opp).sum(axis=1)
    points_for = regular.sum(axis=1)

    # Seeding: wins first, points-for as tiebreaker.
    standing_key = (wins + 0.5 * ties) * _WIN_WEIGHT + points_for
    seed_order = np.argsort(-standing_key, axis=1, kind="stable")  # (n_sims, n_teams)
    seed_rank = np.empty_like(seed_order)
    np.put_along_axis(
        seed_rank, seed_order, np.tile(np.arange(n_teams), (n_sims, 1)), axis=1
    )

    bracket_seeds = seed_order[:, : league.n_playoff_teams]
    champion, finalists = _run_playoffs(bracket_seeds, playoff_scores, seed_rank, rng)

    made_playoffs = (seed_rank < league.n_playoff_teams).mean(axis=0)
    won_title = np.bincount(champion, minlength=n_teams) / n_sims
    reached_final = np.bincount(finalists.ravel(), minlength=n_teams) / n_sims

    # Finish: 0 = champion, 1 = runner-up, then remaining playoff teams by seed,
    # and a final bucket for everyone who missed the playoffs.
    n_places = league.n_playoff_teams + 1
    finish = np.full((n_sims, n_teams), n_places - 1, dtype=np.int64)
    playoff_mask = seed_rank < league.n_playoff_teams
    finish[playoff_mask] = np.minimum(seed_rank[playoff_mask] + 2, n_places - 2)
    np.put_along_axis(finish, finalists, np.ones((n_sims, 2), dtype=np.int64), axis=1)
    np.put_along_axis(finish, champion[:, None], 0, axis=1)

    finish_distribution = np.stack(
        [np.bincount(finish[:, t], minlength=n_places) / n_sims for t in range(n_teams)]
    )

    return SimulationResult(
        team_ids=tuple(t.team_id for t in teams),
        team_names=tuple(t.name for t in teams),
        n_sims=n_sims,
        wins=wins,
        points_for=points_for,
        seed_rank=seed_rank,
        made_playoffs=made_playoffs,
        won_title=won_title,
        reached_final=reached_final,
        finish_distribution=finish_distribution,
        metadata={
            "n_regular_weeks": n_weeks,
            "n_playoff_rounds": n_rounds,
            "n_playoff_teams": league.n_playoff_teams,
        },
    )


def round_robin_schedule(n_teams: int, n_weeks: int) -> np.ndarray:
    """Circle-method round robin, repeated to fill `n_weeks`.

    Real leagues should use the schedule from ESPN's mSettings. This exists for
    tests and for synthetic leagues in the Fantasy Lab.
    """
    if n_teams % 2 != 0:
        raise ValueError(f"round robin requires an even team count, got {n_teams}")

    rotation = list(range(1, n_teams))
    schedule = np.zeros((n_weeks, n_teams), dtype=np.int64)
    for w in range(n_weeks):
        order = [0, *rotation]
        for i in range(n_teams // 2):
            a, b = order[i], order[n_teams - 1 - i]
            schedule[w, a], schedule[w, b] = b, a
        rotation = rotation[1:] + rotation[:1]
    return schedule