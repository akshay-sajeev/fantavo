"""Alternate-lineup and schedule-neutrality what-ifs, backing
`POST /league/{id}/whatif/season-replay`.

Both features need per-week *actual* scores to replay or permute -- data
this codebase does not have anywhere yet. `matchup` (db/migrations/
0001_create_schema.sql) stores only a decidedness flag (`winner`) and a raw
JSONB blob, no score columns, and neither the real league (still pre-draft)
nor the SYNTHETIC validation league (a mock draft, no games "played") has
any actual weekly scoring data. See docs/decisions.md Phase 6 for the full
resolution this module implements:

    ONE sampled "actual" season is drawn from the exact same fitted
    Gamma(mean, sd) x availability model `sim.engine.simulate_seasons()`
    already uses for every simulated season -- via `sim.engine
    ._sample_player_weeks`, the literal helper the engine calls internally,
    not a second sampling implementation. This is a single real draw from
    the real fitted model, not a fabricated point estimate, but it *is* a
    fabricated "what actually happened" -- a different, stronger category
    of claim than a projection or a synthetic roster grouping. Every
    response field this module's output touches is labeled SYNTHETIC (see
    `sim.api.app.SeasonReplayResponse.note`) and this module is never used
    to answer "what happened" for a real, played game.

Alternate lineup: for each team and each week, compares the team's *actual*
starting lineup's realized score against the *optimal* lineup's realized
score -- the best legal assignment of the team's full roster (starters +
bench) to its real starting-slot requirements for that single week, given
that week's already-sampled per-player scores. "Optimal" is solved exactly
via `scipy.optimize.linear_sum_assignment` (the Hungarian algorithm) over a
player x slot-instance cost matrix, with eligibility read directly from
each player's real `eligibleSlots` (CLAUDE.md: "flex eligibility must be
read from eligibleSlots, not inferred") -- not a hand-rolled position-based
heuristic. This is deterministic combinatorial optimization over already-
sampled numbers, not a second simulation path: no new randomness is
introduced here, `rng` is only ever consumed by the one call to
`_sample_player_weeks` below. Both lineups play against the *same* realized
opponent score each week (the opponent's own actual lineup), isolating the
effect of the subject team's own lineup choice.

Schedule neutrality: a pure permutation over the per-team actual weekly
score array already computed for alternate-lineup -- broadcast comparison
against every other team's same-week score, no new sampling, no per-sim
Python loop, exactly matching PLAN.md's "a permutation over existing weekly
score arrays, not a new simulation."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import psycopg
from scipy.optimize import linear_sum_assignment

from ingest.errors import MissingProjectionError, RosterNotAvailableError
from ingest.models import RawTeam
from ingest.parse import parse_player_pool, parse_schedule, parse_scoring_table, parse_teams
from ingest.slots import NON_STARTING_SLOTS, starting_slot_counts
from sim.api.params_loader import load_raw_payload
from sim.engine import PlayerParams, _sample_player_weeks
from sim.params.derive import derive_player_params_pool
from sim.params.variance import fit_position_cv

# Score-scale sentinel for "this player cannot legally fill this slot" in the
# assignment cost matrix. Weekly fantasy scores in this league's real data
# never approach this (see docs/decisions.md Phase 2's ~118-133 mean team
# score sanity check), so it can never be mistaken for a real cost by the
# solver; a genuinely infeasible assignment (impossible for any legitimately
# drafted team, since its own actual lineup is always one feasible solution)
# would surface as this sentinel appearing in the chosen assignment, which
# `_solve_optimal_lineup` checks for explicitly and raises on rather than
# silently returning a wrong "optimal" score.
_INELIGIBLE_COST = 1.0e6


@dataclass(frozen=True)
class TeamSeasonReplay:
    team_id: int
    team_name: str
    actual_wins: int
    actual_losses: int
    actual_ties: int
    optimal_wins: int
    optimal_losses: int
    optimal_ties: int
    actual_points_for: float
    optimal_points_for: float
    # Permutation-derived "if you played everyone, every week" record,
    # rescaled to the same n_regular_weeks games as the actual season (see
    # _schedule_neutral_records) -- fractional, like SimulationResult's own
    # mean_wins, not rounded to an integer.
    neutral_expected_wins: float
    neutral_expected_losses: float
    neutral_expected_ties: float


@dataclass(frozen=True)
class SeasonReplayResult:
    n_regular_weeks: int
    teams: tuple[TeamSeasonReplay, ...]


@dataclass(frozen=True)
class _TeamRosterEntries:
    team_id: int
    team_name: str
    full_player_ids: tuple[int, ...]
    starter_player_ids: tuple[int, ...]


def _load_team_roster_entries(
    raw: dict[str, Any],
    teams: tuple[RawTeam, ...],
    players_by_id: dict[int, PlayerParams],
    non_starting_slots: frozenset[int],
) -> tuple[_TeamRosterEntries, ...]:
    raw_teams_by_id = {t["id"]: t for t in raw["teams"]}
    result: list[_TeamRosterEntries] = []
    for team in teams:
        raw_team = raw_teams_by_id[team.team_id]
        entries = raw_team.get("roster", {}).get("entries", [])
        if not entries:
            raise RosterNotAvailableError(
                f"team {team.team_id} ({team.name}) has no roster entries -- season "
                "replay needs a drafted roster, same as simulate_seasons() itself"
            )
        full_ids: list[int] = []
        starter_ids: list[int] = []
        for entry in entries:
            player_id = entry["playerId"]
            if player_id not in players_by_id:
                raise MissingProjectionError(
                    f"team {team.team_id} ({team.name}) roster includes player "
                    f"{player_id} with no usable projection -- refusing to replay "
                    "a season around a fabricated score"
                )
            full_ids.append(player_id)
            if entry["lineupSlotId"] not in non_starting_slots:
                starter_ids.append(player_id)
        result.append(
            _TeamRosterEntries(
                team_id=team.team_id,
                team_name=raw_team.get("name") or f"Team {team.team_id}",
                full_player_ids=tuple(full_ids),
                starter_player_ids=tuple(starter_ids),
            )
        )
    return tuple(result)


def _solve_optimal_lineup(
    player_ids: tuple[int, ...],
    week_scores: dict[int, float],
    eligible_slots_by_id: dict[int, frozenset[int]],
    slot_instances: tuple[int, ...],
) -> float:
    """Best legal (player -> starting slot) assignment for one team, one
    week, via the Hungarian algorithm. Returns the maximized total score.
    """
    cost = np.full((len(player_ids), len(slot_instances)), _INELIGIBLE_COST)
    for i, player_id in enumerate(player_ids):
        eligible = eligible_slots_by_id[player_id]
        score = week_scores[player_id]
        for j, slot_id in enumerate(slot_instances):
            if slot_id in eligible:
                cost[i, j] = -score

    row_ind, col_ind = linear_sum_assignment(cost)
    chosen = cost[row_ind, col_ind]
    if np.any(chosen >= _INELIGIBLE_COST):
        raise ValueError(
            "no feasible optimal-lineup assignment exists for this roster/slot "
            "shape -- the roster's own actual lineup should always be one "
            "feasible solution, so this indicates a data problem, not a normal "
            "outcome"
        )
    return float(-chosen.sum())


def _schedule_neutral_records(
    actual_weekly_all_teams: npt.NDArray[np.float64],  # (n_teams, n_weeks)
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Vectorized permutation over the actual weekly score matrix: for every
    team and every week, compare against *every other team's* same-week
    score (not just that week's real scheduled opponent), then rescale the
    resulting pairwise win/loss/tie counts down to a season of
    `n_regular_weeks` games each -- so the result reads as a directly
    comparable record to the real (single-opponent) actual record. No `rng`,
    no sampling: a deterministic function of scores already sampled once for
    alternate-lineup.
    """
    n_teams, n_weeks = actual_weekly_all_teams.shape
    scores = actual_weekly_all_teams.T  # (n_weeks, n_teams)

    wins = scores[:, :, None] > scores[:, None, :]  # (n_weeks, n_teams, n_teams)
    ties = scores[:, :, None] == scores[:, None, :]
    diag = np.arange(n_teams)
    wins[:, diag, diag] = False
    ties[:, diag, diag] = False

    total_wins = wins.sum(axis=(0, 2)).astype(np.float64)
    total_ties = ties.sum(axis=(0, 2)).astype(np.float64)
    total_games = n_weeks * (n_teams - 1)
    total_losses = total_games - total_wins - total_ties

    scale = 1.0 / (n_teams - 1)
    return total_wins * scale, total_losses * scale, total_ties * scale


def compute_season_replay(
    conn: psycopg.Connection[Any],
    league_id: int,
    season_id: int,
    *,
    rng: np.random.Generator,
) -> SeasonReplayResult:
    """Sample ONE synthetic "actual" season (see module docstring) and
    compute, per team: actual-vs-optimal-lineup record, and the
    schedule-neutral expected record. `rng` is required, not optional, per
    CLAUDE.md's seeding rule -- it is the only source of randomness this
    module ever touches, consumed exactly once (the single call to
    `_sample_player_weeks` below).
    """
    raw = load_raw_payload(conn, league_id, season_id)

    scoring_table = parse_scoring_table(raw)
    player_pool, _skipped = parse_player_pool(raw, scoring_table)
    eligible_slots_by_id = {p.player_id: p.eligible_slots for p in player_pool}
    position_cv = fit_position_cv(raw)
    players_by_id = derive_player_params_pool(player_pool, position_cv)

    teams = parse_teams(raw)
    schedule_settings = raw["settings"]["scheduleSettings"]
    n_regular_weeks = int(schedule_settings["matchupPeriodCount"])
    schedule = parse_schedule(raw, teams, n_regular_weeks)

    slot_requirements = starting_slot_counts(raw["settings"]["rosterSettings"]["lineupSlotCounts"])
    slot_instances = tuple(
        slot_id for slot_id, count in slot_requirements.items() for _ in range(count)
    )

    rosters = _load_team_roster_entries(raw, teams, players_by_id, NON_STARTING_SLOTS)

    # ONE realization of every rostered player's weekly score, in one rng
    # draw -- see module docstring for why this is a single sample, not a
    # fitted or invented value.
    flat_ids = [pid for r in rosters for pid in r.full_player_ids]
    flat_players = tuple(players_by_id[pid] for pid in flat_ids)
    sampled = _sample_player_weeks(flat_players, n_sims=1, n_weeks=n_regular_weeks, rng=rng)[0]
    # sampled: (n_weeks, len(flat_ids))

    scores_by_player: dict[int, npt.NDArray[np.float64]] = {}
    offset = 0
    for r in rosters:
        for i, pid in enumerate(r.full_player_ids):
            scores_by_player[pid] = sampled[:, offset + i]
        offset += len(r.full_player_ids)

    n_teams = len(rosters)
    actual_weekly = np.zeros((n_teams, n_regular_weeks))
    optimal_weekly = np.zeros((n_teams, n_regular_weeks))
    for t, r in enumerate(rosters):
        for pid in r.starter_player_ids:
            actual_weekly[t] += scores_by_player[pid]
        for w in range(n_regular_weeks):
            week_scores = {pid: float(scores_by_player[pid][w]) for pid in r.full_player_ids}
            optimal_weekly[t, w] = _solve_optimal_lineup(
                r.full_player_ids, week_scores, eligible_slots_by_id, slot_instances
            )

    neutral_wins, neutral_losses, neutral_ties = _schedule_neutral_records(actual_weekly)

    results: list[TeamSeasonReplay] = []
    for t, r in enumerate(rosters):
        opp_actual = actual_weekly[schedule.pairings[:, t], np.arange(n_regular_weeks)]
        actual_w = int((actual_weekly[t] > opp_actual).sum())
        actual_l = int((actual_weekly[t] < opp_actual).sum())
        actual_tie = n_regular_weeks - actual_w - actual_l
        optimal_w = int((optimal_weekly[t] > opp_actual).sum())
        optimal_l = int((optimal_weekly[t] < opp_actual).sum())
        optimal_tie = n_regular_weeks - optimal_w - optimal_l

        results.append(
            TeamSeasonReplay(
                team_id=r.team_id,
                team_name=r.team_name,
                actual_wins=actual_w,
                actual_losses=actual_l,
                actual_ties=actual_tie,
                optimal_wins=optimal_w,
                optimal_losses=optimal_l,
                optimal_ties=optimal_tie,
                actual_points_for=float(actual_weekly[t].sum()),
                optimal_points_for=float(optimal_weekly[t].sum()),
                neutral_expected_wins=float(neutral_wins[t]),
                neutral_expected_losses=float(neutral_losses[t]),
                neutral_expected_ties=float(neutral_ties[t]),
            )
        )

    return SeasonReplayResult(n_regular_weeks=n_regular_weeks, teams=tuple(results))
