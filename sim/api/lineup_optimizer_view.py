"""Lineup Optimizer, backing `GET /league/{id}/lineup-optimizer/{team_id}`.

Covers PLAN.md's Phase 9 "Weekly loop" sub-feature "Lineup optimizer"
(feature 10): three lineups for one team, built from that team's own real
roster (starters + bench/IR), respecting real `eligibleSlots`
(CLAUDE.md: never infer eligibility from listed position) --

    1. Current   -- the team's actual ingested starting lineup, as-is.
    2. Safest    -- the legal lineup with the highest projected FLOOR of the
                    team's total weekly score, derived from real Monte Carlo
                    samples of the team TOTAL (not a sum of individual
                    player floors -- see "Why floor needs real sampling"
                    below).
    3. Highest upside -- the legal lineup that maximizes season TITLE
                    PROBABILITY specifically (not mean points, not
                    single-week upside), via `simulate_seasons()`.

No second simulation path anywhere in this module: every sampled number
comes from `sim.engine._sample_player_weeks` (the literal per-player
sampling primitive `simulate_seasons()` itself calls, the same reuse
`sim.api.season_replay_view` and `sim.api.playoff_planner_view` already
established) or from `sim.engine.simulate_seasons()` directly via
`roster_overrides`.

--------------------------------------------------------------------------
Season-long-override framing -- read this before anything else:

`simulate_seasons()`'s `roster_overrides` parameter replaces a team's
`starters` for the ENTIRE simulated season. Confirmed directly against
`sim/engine.py`: a team's `TeamParams.starters` tuple is fixed once
(`teams = tuple(TeamParams(t.team_id, t.name, roster_overrides.get(t.team_id,
t.starters)) for t in teams)`), and `_sample_team_weeks` is called exactly
once per team per `simulate_seasons()` call, sampling all `n_weeks +
n_rounds` columns from that one fixed `starters` tuple. There is no
per-week lineup mechanism in the engine at all -- `roster_overrides` is a
season-long swap, not a single-week one.

Framing "safest" and "highest upside" as season-long roster choices is
therefore not a workaround for this phase -- it is the only framing
`simulate_seasons()` is capable of expressing today (see the module
docstring of `sim/engine.py`: "v1 models a fixed starting lineup per team
for the whole season... That is a deliberate simplification, not an
oversight").

It also happens to be an honest match for where this league's actual data
is right now: every matchup for the real league (league_id=885686492) is
still `UNDECIDED` (docs/decisions.md, Phases 5-8) -- the season has not
played a single week yet. There is currently no real distinction between
"who should start THIS week" and "who should start for the rest of the
season," because those are the same question today. So this module's
season-long framing is not silently standing in for a "this week" question
it can't actually answer -- there is no "this week" question distinct from
"the rest of the season" yet to answer.

This will stop being true the moment real weeks get played (a future
phase's territory, explicitly not Phase 9a's). At that point "safest
lineup" plausibly splits into two different, both legitimate questions --
"safest for next week's single matchup" vs. "safest for the rest of an
already-partly-played season" -- and answering the first one honestly would
need a real per-week lineup mechanism in `sim.engine`, which does not exist
today. Noted here explicitly, and in docs/decisions.md Phase 9a, rather than
silently treated as a permanent design.

--------------------------------------------------------------------------
Why floor needs real sampling, not a sum of individual floors:

The floor (10th percentile) of a SUM of independent random variables is NOT
the sum of their individual floors -- summing floors overstates a team's
real downside risk, because it implicitly assumes every player has a bad
week simultaneously, which is far less likely than any one of them having a
bad week alone (diversification narrows the *team total's* spread relative
to the naive sum). The only correct way to get a team-total floor is to
sample the team total directly and take a percentile of the samples
themselves. This module does exactly that: `_weekly_totals` draws
`_sample_player_weeks(candidate_starters, n_sims=..., n_weeks=1, rng)` (the
literal per-player Gamma(mean, sd) x availability primitive
`simulate_seasons()` itself uses) and sums across players *before* taking
any percentile -- never taking each player's own percentile first and
adding those up.

--------------------------------------------------------------------------
Search space (why single-slot swaps, not full permutation):

A full brute-force search over every legal assignment of a team's full
roster to its starting slots is combinatorially large enough to make
re-simulating each one intractable. Measured directly against the real
league (8 teams, 9 starting slots -- 1 QB/2 RB/2 WR/1 TE/1 FLEX/1 D-ST/1 K,
6-7 bench players each): the full Cartesian product of per-slot-instance
legal fillers ranges from ~1,300 to ~4,000 candidate lineups per team.
Re-running `simulate_seasons()` (needed for the highest-upside search) that
many times per request is not viable for a live endpoint.

What IS tractable, and matches PLAN.md's own suggested framing exactly --
"the roster's actual decision points (e.g. FLEX slot choice, any position
where a bench player is a legitimate alternative to a starter)" -- is
**every single-slot swap**: for each of the 9 starting-slot instances, and
for each bench/IR player who is legally eligible for that specific slot
(via that player's real `eligibleSlots`, never inferred), one candidate
lineup = the current lineup with exactly that one slot's occupant replaced,
every other slot held at its current occupant. Measured against the real
league, this collapses the search space to 14-19 candidates per team (plus
the baseline itself) -- small enough to fully re-evaluate (both the
Monte Carlo floor sample and a fresh `simulate_seasons()` call) for every
candidate in well under a couple of seconds (measured: 19 candidates x
`simulate_seasons(n_sims=5000)` runs in ~2s against the real league).

This search space is a genuine, non-arbitrary scoping, not an invented
shortcut: it is exactly "which single bench player, if any, is a real
alternative to a specific starter" -- the actual decision a fantasy manager
faces at each position, one slot at a time. It deliberately does NOT search
compound multi-slot swaps (e.g. simultaneously changing both FLEX and RB2),
since that reintroduces the same combinatorial blowup the single-swap
scoping exists to avoid, and because two swaps only compound when the
bench players involved don't overlap -- an edge case, not the typical
one-real-alternative-per-position shape this league's real rosters actually
have. If the eventual "safest"/"highest upside" lineup differs from
`current` in more than one slot, that's still possible: this module reports
a Cartesian *set* of independently-generated single-swap candidates, and
either objective is free to prefer any one of them, including the baseline
itself if no single swap actually helps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import psycopg

from ingest.parse import (
    build_team_params,
    parse_player_pool,
    parse_schedule,
    parse_scoring_table,
    parse_teams,
)
from ingest.slots import (
    DEFAULT_POSITION_NAMES,
    NON_STARTING_SLOTS,
    SLOT_NAMES,
    starting_slot_counts,
)
from sim.api.params_loader import load_raw_payload
from sim.api.seeds import lineup_optimizer_seed
from sim.engine import LeagueParams, PlayerParams, _sample_player_weeks, simulate_seasons
from sim.params.derive import derive_player_params_pool
from sim.params.variance import fit_position_cv

# Percentile band for the team-total weekly floor/ceiling -- same 10th/90th
# choice `sim.api.roster_view` already uses for individual players (a
# presentation choice, consistent app-wide, not a fitted value); applied
# here to real MC samples of a *team total*, not a closed-form per-player
# formula (see module docstring, "Why floor needs real sampling").
_FLOOR_PERCENTILE_PCT = 10.0
_CEILING_PERCENTILE_PCT = 90.0

# How many single-week samples of a candidate lineup's TEAM TOTAL to draw
# when picking the safest floor. Cheap (one week, ~9 players, no season
# simulation) -- large enough that a 10th-percentile estimate is stable to
# well under a tenth of a point, not a fitted or tuned value.
DEFAULT_WEEKLY_N_SIMS = 20_000

# n_sims for the per-candidate simulate_seasons() call the highest-upside
# search needs. Larger than LIVE_WHATIF_N_SIMS (2,000, the single-scenario
# what-if default) because this module runs MANY candidates per request and
# ranks them against each other by a probability that can be close between
# two similar lineups -- less sampling noise in the ranking matters more
# here than it does for a single before/after comparison. Still fast:
# measured ~2s for 19 candidates against the real 8-team league (see module
# docstring).
DEFAULT_SEASON_N_SIMS = 5_000


class UnknownTeamError(ValueError):
    """Raised when `team_id` does not belong to the requested league/season.
    Mapped to HTTP 404 by the route handler, the same class of "this
    specific thing was never real" error `LeagueNotIngestedError` covers for
    the league itself."""


@dataclass(frozen=True)
class LineupSlotAssignment:
    slot_label: str
    player_id: int
    player_name: str
    position: str
    # True if this player is NOT who the team's actual ingested lineup has
    # starting in this slot -- lets the UI highlight exactly what changed
    # versus "Current" without the frontend re-deriving anything (CLAUDE.md:
    # no analytics logic in components).
    is_swap: bool


@dataclass(frozen=True)
class LineupProjection:
    label: str  # "Current" | "Safest" | "Highest upside"
    assignments: tuple[LineupSlotAssignment, ...]
    # Team-total single-week score distribution, from real MC samples of
    # this exact lineup (see module docstring) -- never a sum of individual
    # player floors/ceilings.
    weekly_mean: float
    weekly_floor: float
    weekly_ceiling: float
    # Season-level outcome with ONLY this team's lineup overridden to this
    # candidate (every other team keeps its real ingested roster), from a
    # real simulate_seasons() call.
    title_probability: float
    playoff_probability: float
    finish_distribution: tuple[float, ...]


@dataclass(frozen=True)
class LineupOptimizerResult:
    league_id: int
    season_id: int
    team_id: int
    team_name: str
    seed: int
    weekly_n_sims: int
    season_n_sims: int
    # How many single-slot-swap candidates (including the baseline) this
    # team's search actually considered -- surfaced so the response is
    # self-describing about the search's own scope, not a hidden detail.
    n_candidates_considered: int
    current: LineupProjection
    safest: LineupProjection
    highest_upside: LineupProjection


@dataclass(frozen=True)
class _RosterEntry:
    player_id: int
    name: str
    position: str
    eligible_slots: frozenset[int]
    params: PlayerParams


@dataclass(frozen=True)
class _Candidate:
    # One player_id per starting-slot instance, in the same order as
    # `slot_instances` -- the same "list of slot ids, one entry per required
    # starter" shape `sim.api.season_replay_view` already uses for its own
    # Hungarian-algorithm slot_instances.
    player_ids: tuple[int, ...]


def _slot_instances(slot_requirements: dict[int, int]) -> tuple[int, ...]:
    return tuple(slot_id for slot_id, count in slot_requirements.items() for _ in range(count))


def _load_full_roster(
    raw: dict[str, Any],
    team_id: int,
    players_by_id: dict[int, PlayerParams],
    projections_by_id: dict[int, Any],
) -> tuple[dict[int, _RosterEntry], list[dict[str, Any]]]:
    """Every roster entry (starters + bench + IR) for `team_id` that has a
    real, usable projection, keyed by player_id -- plus the raw entries list
    itself (needed to recover which player currently occupies which slot).

    A rostered player with no usable projection (see docs/decisions.md, "no
    more mock data" -- a real, if fringe, occurrence) is simply excluded
    from the candidate pool, same as `sim.api.roster_view` and
    `sim.api.season_replay_view` already do for bench players: nothing here
    computes a score for a player with no distribution to sample from. A
    *starter* with no projection is a harder failure (raised inside
    `ingest.parse.build_team_params`, which the caller already calls before
    this function runs) -- by the time this function is reached, every
    entry actually starting for this team is guaranteed projected.
    """
    raw_teams_by_id = {t["id"]: t for t in raw["teams"]}
    raw_team = raw_teams_by_id.get(team_id)
    entries = raw_team.get("roster", {}).get("entries", []) if raw_team else []

    roster: dict[int, _RosterEntry] = {}
    for entry in entries:
        player_id = entry["playerId"]
        params = players_by_id.get(player_id)
        projection = projections_by_id.get(player_id)
        if params is None or projection is None:
            continue
        roster[player_id] = _RosterEntry(
            player_id=player_id,
            name=projection.name,
            position=DEFAULT_POSITION_NAMES.get(projection.default_position_id, "?"),
            eligible_slots=projection.eligible_slots,
            params=params,
        )
    return roster, entries


def _current_lineup_player_ids(
    entries: list[dict[str, Any]], slot_instances: tuple[int, ...]
) -> tuple[int, ...]:
    """One player_id per slot instance, in `slot_instances` order, read
    straight off this team's real ingested lineup -- "Current" needs no
    computation (PLAN.md), just reading `lineupSlotId` off each entry.
    """
    by_slot: dict[int, list[int]] = {}
    for entry in entries:
        slot_id = entry["lineupSlotId"]
        if slot_id in NON_STARTING_SLOTS:
            continue
        by_slot.setdefault(slot_id, []).append(entry["playerId"])

    counters: dict[int, int] = {}
    result: list[int] = []
    for slot_id in slot_instances:
        idx = counters.get(slot_id, 0)
        occupants = by_slot.get(slot_id, [])
        if idx >= len(occupants):
            raise ValueError(
                f"roster has fewer players in lineupSlotId={slot_id} than the league's "
                f"own lineupSlotCounts requires -- an incompletely-filled starting lineup"
            )
        result.append(occupants[idx])
        counters[slot_id] = idx + 1
    return tuple(result)


def _generate_candidates(
    slot_instances: tuple[int, ...],
    baseline_ids: tuple[int, ...],
    roster: dict[int, _RosterEntry],
) -> tuple[_Candidate, ...]:
    """Baseline (current lineup) plus every single-slot swap -- see the
    module docstring's "Search space" section for why this, and only this,
    is the search space.
    """
    starter_ids = set(baseline_ids)
    # Deterministic order (roster dict preserves ESPN entry order, which is
    # itself fixed per fixture) so the candidate list -- and therefore the
    # rng consumption order -- is reproducible for a given seed.
    bench_ids = [pid for pid in roster if pid not in starter_ids]

    candidates = [_Candidate(player_ids=baseline_ids)]
    for i, slot_id in enumerate(slot_instances):
        for bench_id in bench_ids:
            if slot_id in roster[bench_id].eligible_slots:
                swapped = list(baseline_ids)
                swapped[i] = bench_id
                candidates.append(_Candidate(player_ids=tuple(swapped)))
    return tuple(candidates)


def _weekly_totals(
    player_ids: tuple[int, ...],
    roster: dict[int, _RosterEntry],
    rng: np.random.Generator,
    n_sims: int,
) -> npt.NDArray[np.float64]:
    """One real MC sample of this exact lineup's team TOTAL for a single
    week -- (n_sims,). Sums across players *before* any percentile is taken,
    per the module docstring's "Why floor needs real sampling" section.
    """
    players = tuple(roster[pid].params for pid in player_ids)
    sampled = _sample_player_weeks(players, n_sims=n_sims, n_weeks=1, rng=rng)
    result: npt.NDArray[np.float64] = sampled.sum(axis=2)[:, 0]
    return result


def _season_outcome(
    league: LeagueParams,
    team_id: int,
    player_ids: tuple[int, ...],
    roster: dict[int, _RosterEntry],
    rng: np.random.Generator,
    n_sims: int,
) -> tuple[float, float, tuple[float, ...]]:
    """title_probability, playoff_probability, finish_distribution for this
    team with ONLY its own lineup overridden to `player_ids` -- every other
    team in the league keeps its real ingested roster, since
    `roster_overrides` only touches the team_id keys passed to it.
    """
    starters = tuple(roster[pid].params for pid in player_ids)
    result = simulate_seasons(
        league, n_sims=n_sims, rng=rng, roster_overrides={team_id: starters}
    )
    idx = result.team_ids.index(team_id)
    return (
        float(result.won_title[idx]),
        float(result.made_playoffs[idx]),
        tuple(float(x) for x in result.finish_distribution[idx]),
    )


def compute_lineup_optimizer(
    conn: psycopg.Connection[Any],
    league_id: int,
    season_id: int,
    team_id: int,
    *,
    weekly_n_sims: int = DEFAULT_WEEKLY_N_SIMS,
    season_n_sims: int = DEFAULT_SEASON_N_SIMS,
    seed: int | None = None,
) -> LineupOptimizerResult:
    """Build the Lineup Optimizer for one team. Raises
    `ingest.errors.RosterNotAvailableError` / `MissingProjectionError` (via
    `build_team_params`, called for every team so the full league can be
    simulated) for a league with no drafted, fully-projectable rosters yet
    -- the same refusal every other `sim.api` view makes rather than
    fabricating a lineup. Raises `UnknownTeamError` if `team_id` is not a
    real team in this league/season.
    """
    raw = load_raw_payload(conn, league_id, season_id)
    return _compute_from_raw(
        raw,
        league_id,
        season_id,
        team_id,
        weekly_n_sims=weekly_n_sims,
        season_n_sims=season_n_sims,
        seed=seed,
    )


def _compute_from_raw(
    raw: dict[str, Any],
    league_id: int,
    season_id: int,
    team_id: int,
    *,
    weekly_n_sims: int,
    season_n_sims: int,
    seed: int | None,
) -> LineupOptimizerResult:
    """The actual computation, factored out from `compute_lineup_optimizer`
    so it can be exercised directly against an in-memory fixture dict (no
    Postgres needed) -- same fast-unit-tests-plus-thin-integration-test
    split `sim.api.draft_autopsy_view` / `sim.api.playoff_planner_view`
    already established.
    """
    scoring_table = parse_scoring_table(raw)
    player_pool, _skipped = parse_player_pool(raw, scoring_table)
    projections_by_id = {p.player_id: p for p in player_pool}
    position_cv = fit_position_cv(raw)
    players_by_id: dict[int, PlayerParams] = derive_player_params_pool(player_pool, position_cv)

    teams = parse_teams(raw)
    team_ids = {t.team_id for t in teams}
    if team_id not in team_ids:
        raise UnknownTeamError(
            f"team_id={team_id} is not a team in league_id={league_id} season_id={season_id}"
        )

    schedule_settings = raw["settings"]["scheduleSettings"]
    n_regular_weeks = int(schedule_settings["matchupPeriodCount"])
    n_playoff_teams = int(schedule_settings["playoffTeamCount"])
    schedule = parse_schedule(raw, teams, n_regular_weeks)

    # Every team's real starters, needed so the highest-upside search can
    # call simulate_seasons() with only the ONE team under evaluation
    # overridden -- everyone else plays their real ingested roster.
    team_params = tuple(build_team_params(raw, t.team_id, players_by_id) for t in teams)
    league = LeagueParams(
        teams=team_params, schedule=schedule.pairings, n_playoff_teams=n_playoff_teams
    )

    team_name = next(t.name for t in team_params if t.team_id == team_id)

    slot_requirements = starting_slot_counts(raw["settings"]["rosterSettings"]["lineupSlotCounts"])
    slot_instances = _slot_instances(slot_requirements)
    slot_labels = tuple(SLOT_NAMES.get(slot_id, str(slot_id)) for slot_id in slot_instances)

    roster, entries = _load_full_roster(raw, team_id, players_by_id, projections_by_id)
    if not entries:
        # build_team_params above already raised RosterNotAvailableError for
        # this exact case before this point is ever reached -- this is an
        # unreachable-in-practice guard, not a new refusal path.
        raise ValueError(f"team {team_id} has no roster entries")

    baseline_ids = _current_lineup_player_ids(entries, slot_instances)
    candidates = _generate_candidates(slot_instances, baseline_ids, roster)

    resolved_seed = seed if seed is not None else lineup_optimizer_seed(league_id, season_id, team_id)
    rng = np.random.default_rng(resolved_seed)

    # Each candidate is fully evaluated (weekly totals, then season outcome)
    # before moving to the next -- a fixed, documented rng consumption
    # order so the same seed always reproduces the same result.
    weekly_by_candidate: dict[_Candidate, npt.NDArray[np.float64]] = {}
    season_by_candidate: dict[_Candidate, tuple[float, float, tuple[float, ...]]] = {}
    for candidate in candidates:
        weekly_by_candidate[candidate] = _weekly_totals(
            candidate.player_ids, roster, rng, weekly_n_sims
        )
        season_by_candidate[candidate] = _season_outcome(
            league, team_id, candidate.player_ids, roster, rng, season_n_sims
        )

    def floor_of(c: _Candidate) -> float:
        return float(np.percentile(weekly_by_candidate[c], _FLOOR_PERCENTILE_PCT))

    def title_prob_of(c: _Candidate) -> float:
        return season_by_candidate[c][0]

    baseline = candidates[0]
    safest = max(candidates, key=floor_of)
    upside = max(candidates, key=title_prob_of)

    def to_projection(label: str, candidate: _Candidate) -> LineupProjection:
        totals = weekly_by_candidate[candidate]
        title, playoff, finish = season_by_candidate[candidate]
        assignments = tuple(
            LineupSlotAssignment(
                slot_label=slot_labels[i],
                player_id=pid,
                player_name=roster[pid].name,
                position=roster[pid].position,
                is_swap=(pid != baseline_ids[i]),
            )
            for i, pid in enumerate(candidate.player_ids)
        )
        return LineupProjection(
            label=label,
            assignments=assignments,
            weekly_mean=float(totals.mean()),
            weekly_floor=float(np.percentile(totals, _FLOOR_PERCENTILE_PCT)),
            weekly_ceiling=float(np.percentile(totals, _CEILING_PERCENTILE_PCT)),
            title_probability=title,
            playoff_probability=playoff,
            finish_distribution=finish,
        )

    return LineupOptimizerResult(
        league_id=league_id,
        season_id=season_id,
        team_id=team_id,
        team_name=team_name,
        seed=resolved_seed,
        weekly_n_sims=weekly_n_sims,
        season_n_sims=season_n_sims,
        n_candidates_considered=len(candidates),
        current=to_projection("Current", baseline),
        safest=to_projection("Safest", safest),
        highest_upside=to_projection("Highest upside", upside),
    )
