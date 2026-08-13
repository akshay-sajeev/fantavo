"""Thin FastAPI service exposing `simulate_seasons()` over HTTP.

Every route handler here does exactly three things: parse the request, load
params (`sim.api.params_loader`, ultimately backed by `ingest.parse` +
`sim.params` against an already-ingested league), and call
`sim.engine.simulate_seasons()`. No analytics or derivation logic lives in
this module -- CLAUDE.md's "one simulation engine" and "no analytics logic
in route handlers" rules both apply directly here.

    GET  /league/{league_id}/simulation   cached precomputed results
    POST /league/{league_id}/whatif       live, n_sims=2000, roster overrides
    GET  /league/{league_id}/roster       team rosters + per-player risk metrics
    GET  /league/{league_id}/schedule     regular-season schedule + current week
    GET  /league/{league_id}/lineup-optimizer/{team_id}
                                           current/safest/highest-upside lineups

Both routes accept the ingest schema's real grain: an optional `season_id`
(query param on GET, body field on POST). If omitted, the most recently
ingested season for that league is used (see
`params_loader.resolve_season_id`) -- the plan's URL shape has no season in
it, but `league_id` alone is not a primary key in this schema (Phase 3 keyed
everything by (league_id, season_id) on purpose).

`/roster` and `/schedule` were added in Phase 5b, additively, to serve the
dashboard and risk-panel UI: both are pure read serializations of data
already computed in memory by `sim.api.roster_view` / `sim.api.schedule_view`
(themselves built from the same `ingest.parse` / `sim.params` pipeline, or
straight off the already-normalized `matchup`/`team` tables) -- neither adds
a new simulation path, calls `simulate_seasons()` differently, or invents
any value. See those two modules' docstrings for exactly what each derived
field means and why it isn't "analytics logic" in the CLAUDE.md sense.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import numpy as np
import psycopg
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from ingest.db import DEFAULT_DEV_DSN, connect, dsn_from_env
from ingest.errors import IngestError
from sim.api.cache import read_cached_simulation, serialize_result
from sim.api.draft_autopsy_view import (
    DraftAutopsy,
    DraftPickGrade,
    PositionGrade,
    PositionTiming,
    TeamDraftAutopsy,
    compute_draft_autopsy,
)
from sim.api.lineup_optimizer_view import (
    LineupOptimizerResult,
    LineupProjection,
    LineupSlotAssignment,
    UnknownTeamError,
    compute_lineup_optimizer,
)
from sim.api.params_loader import LeagueNotIngestedError, load_league, resolve_season_id
from sim.api.playoff_planner_view import (
    BracketMatchup,
    PlayoffPlannerResult,
    PlayoffSeedOdds,
    SlotPlayoffStrength,
    TeamPlayoffPlan,
    compute_playoff_planner,
)
from sim.api.roster_view import RosterPlayer, TeamRosterView, load_team_rosters
from sim.api.schedule_view import ScheduledMatchup, load_schedule
from sim.api.scheduler import start_scheduler
from sim.api.season_replay_view import TeamSeasonReplay, compute_season_replay
from sim.api.seeds import draw_whatif_seed
from sim.engine import PlayerParams, simulate_seasons
from sim.params.errors import ParamsError

logger = logging.getLogger(__name__)

LIVE_WHATIF_N_SIMS = 2_000

# Errors that mean "the request can't be satisfied with real data" rather
# than a server bug -- translated to a 4xx with the original message instead
# of a bare 500, per CLAUDE.md's "no invented numbers: raise and say so"
# rule applied to the HTTP layer.
_DATA_UNAVAILABLE_ERRORS = (IngestError, ParamsError)


class TeamOutcome(BaseModel):
    """One team's simulated outcome distribution -- never a bare scalar.
    `finish_distribution[p]` is the probability of finishing in place `p`
    (0 = champion), matching `sim.engine.SimulationResult.finish_distribution`.
    """

    team_id: int
    team_name: str
    title_probability: float
    playoff_probability: float
    reached_final_probability: float
    mean_wins: float
    mean_points_for: float
    finish_distribution: list[float]


class SimulationResponse(BaseModel):
    league_id: int
    season_id: int
    n_sims: int
    seed: int
    computed_at: datetime
    teams: list[TeamOutcome]


class WhatIfRequest(BaseModel):
    season_id: int | None = None
    # team_id -> ordered list of player_ids to use as that team's starters
    # instead of its ingested roster. Every player_id must already have a
    # derived PlayerParams for this league/season (i.e. be in the ingested
    # free-agent/player pool) -- see the 422 raised below otherwise.
    roster_overrides: dict[int, list[int]] = Field(default_factory=dict)
    n_sims: int = LIVE_WHATIF_N_SIMS
    # Explicit seed for a reproducible re-run of this exact scenario. Drawn
    # from OS entropy (sim.api.seeds.draw_whatif_seed) if omitted -- see
    # that module's docstring for the full seeding rationale.
    seed: int | None = None


class RosterPlayerOut(BaseModel):
    """One rostered player plus their per-game risk metrics. `floor`/
    `ceiling` are the 10th/90th percentile of the same per-game
    Gamma(mean, sd) distribution the engine samples from -- see
    `sim.api.roster_view` for the exact derivation.

    Numeric fields are null when `has_projection` is false: a bench/IR
    player ESPN has no usable season projection for. Never null for a
    starter -- see `sim.api.roster_view.load_team_rosters`."""

    player_id: int
    name: str
    position: str
    lineup_slot: str
    is_starter: bool
    has_projection: bool
    mean: float | None
    sd: float | None
    availability: float | None
    floor: float | None
    ceiling: float | None


class TeamRosterOut(BaseModel):
    team_id: int
    team_name: str
    starters: list[RosterPlayerOut]
    bench: list[RosterPlayerOut]
    risk_rating: float
    positional_concentration: list[str]


class RosterResponse(BaseModel):
    league_id: int
    season_id: int
    teams: list[TeamRosterOut]


class ScheduledMatchupOut(BaseModel):
    week: int
    home_team_id: int | None
    home_team_name: str | None
    away_team_id: int | None
    away_team_name: str | None
    winner: str | None


class ScheduleResponse(BaseModel):
    league_id: int
    season_id: int
    n_regular_weeks: int
    # None if the schedule table has no undecided matchup left -- see
    # sim.api.schedule_view's docstring for the exact rule. Never a
    # fabricated week number.
    current_week: int | None
    weeks: list[list[ScheduledMatchupOut]]


class SeasonReplayRequest(BaseModel):
    season_id: int | None = None
    # Same explicit-or-drawn seeding convention as WhatIfRequest.seed (see
    # sim.api.seeds) -- pass one back to reproduce an earlier replay exactly.
    seed: int | None = None


class SeasonReplayTeamOut(BaseModel):
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
    neutral_expected_wins: float
    neutral_expected_losses: float
    neutral_expected_ties: float


# CLAUDE.md's "no invented numbers" rule squarely applies to this note: this
# whole response is one sampled realization standing in for "what actually
# happened," not a real result -- see sim.api.season_replay_view's module
# docstring and docs/decisions.md Phase 6 for the full resolution.
_SEASON_REPLAY_NOTE = (
    "SYNTHETIC simulated season, not real results. No real weekly scores have "
    "been ingested for this league yet, so this replay uses one sampled "
    "realization drawn from the same fitted per-player scoring model "
    "simulate_seasons() uses everywhere else, purely to exercise the "
    "alternate-lineup and schedule-neutrality what-ifs end-to-end."
)


class SeasonReplayResponse(BaseModel):
    league_id: int
    season_id: int
    n_regular_weeks: int
    seed: int
    synthetic_actual_scores: bool = True
    note: str = _SEASON_REPLAY_NOTE
    teams: list[SeasonReplayTeamOut]


class DraftPickGradeOut(BaseModel):
    """One real draft pick, graded against the specific same-position
    alternative still on the board at that pick -- see
    `sim.api.draft_autopsy_view.DraftPickGrade` for the exact meaning of
    `value_gap` (positive = correct-process pick, negative = a reach past a
    better-ranked alternative at the same position, zero with no alternative
    id = nothing left in the tracked pool to compare against)."""

    overall_pick_number: int
    round_id: int
    round_pick_number: int
    team_id: int
    team_name: str
    player_id: int
    player_name: str
    position: str
    slot_label: str
    grade_bucket: str | None
    player_rank: int
    player_adp: float | None
    alternative_player_id: int | None
    alternative_player_name: str | None
    alternative_player_rank: int | None
    value_gap: float
    best_overall_available_player_id: int | None
    best_overall_available_player_name: str | None
    best_overall_available_rank: int | None


class PositionGradeOut(BaseModel):
    position: str
    pick_count: int
    avg_value_gap: float
    league_avg_value_gap: float
    label: str


class PositionTimingOut(BaseModel):
    position: str
    team_first_pick_number: int
    team_first_pick_round: int
    league_avg_first_pick_number: float
    team_pick_count: int
    team_avg_value_gap: float
    league_avg_value_gap: float


class TeamDraftAutopsyOut(BaseModel):
    team_id: int
    team_name: str
    picks: list[DraftPickGradeOut]
    best_pick: DraftPickGradeOut
    worst_pick: DraftPickGradeOut
    position_grades: list[PositionGradeOut]
    position_timing: list[PositionTimingOut]
    structural_finding: str


class DraftAutopsyResponse(BaseModel):
    league_id: int
    season_id: int
    # Human-readable label for the rank signal used throughout -- see
    # sim.api.draft_autopsy_view's module docstring and docs/decisions.md
    # Phase 7 for the full data-provenance reasoning.
    rank_source: str
    teams: list[TeamDraftAutopsyOut]


class SlotPlayoffStrengthOut(BaseModel):
    """One starting slot's playoff-window strength -- see
    `sim.api.playoff_planner_view.SlotPlayoffStrength` for the exact
    meaning of `floor_ratio_delta` (the floor-ratio gap that can appear only
    because the playoff window is short, not because the underlying weekly
    distribution changed)."""

    slot_label: str
    regular_mean_points_per_week: float
    playoff_mean_points_per_week: float
    regular_floor_points_per_week: float
    playoff_floor_points_per_week: float
    regular_floor_ratio: float
    playoff_floor_ratio: float
    floor_ratio_delta: float
    has_bench_depth: bool
    league_rank: int
    league_team_count: int
    league_percentile: float
    is_playoff_specific_weakness: bool


class PlayoffSeedOddsOut(BaseModel):
    team_id: int
    team_name: str
    title_probability: float
    playoff_probability: float
    reached_final_probability: float
    finish_distribution: list[float]
    seed_probabilities: list[float]
    projected_seed: int | None


class BracketMatchupOut(BaseModel):
    high_seed: int
    high_seed_team_id: int | None
    high_seed_team_name: str | None
    low_seed: int
    low_seed_team_id: int | None
    low_seed_team_name: str | None


class TeamPlayoffPlanOut(BaseModel):
    team_id: int
    team_name: str
    slot_strengths: list[SlotPlayoffStrengthOut]
    weakest_slot: str | None
    recommendation: str


class PlayoffPlannerResponse(BaseModel):
    league_id: int
    season_id: int
    n_regular_weeks: int
    n_playoff_rounds: int
    n_playoff_teams: int
    seed: int
    n_sims: int
    seeding: list[PlayoffSeedOddsOut]
    bracket: list[BracketMatchupOut]
    teams: list[TeamPlayoffPlanOut]


class LineupSlotAssignmentOut(BaseModel):
    slot_label: str
    player_id: int
    player_name: str
    position: str
    is_swap: bool


class LineupProjectionOut(BaseModel):
    """One of the three lineups (Current / Safest / Highest upside) for a
    team -- see `sim.api.lineup_optimizer_view.LineupProjection` for the
    exact meaning of every field, in particular `weekly_floor`/
    `weekly_ceiling` (real Monte Carlo samples of this lineup's team TOTAL,
    never a sum of individual player floors/ceilings) and
    `title_probability` (from a real `simulate_seasons()` call with only
    this team's lineup overridden to this candidate)."""

    label: str
    assignments: list[LineupSlotAssignmentOut]
    weekly_mean: float
    weekly_floor: float
    weekly_ceiling: float
    title_probability: float
    playoff_probability: float
    finish_distribution: list[float]


class LineupOptimizerResponse(BaseModel):
    league_id: int
    season_id: int
    team_id: int
    team_name: str
    seed: int
    weekly_n_sims: int
    season_n_sims: int
    n_candidates_considered: int
    current: LineupProjectionOut
    safest: LineupProjectionOut
    highest_upside: LineupProjectionOut


def _to_roster_player_out(player: RosterPlayer) -> RosterPlayerOut:
    return RosterPlayerOut(
        player_id=player.player_id,
        name=player.name,
        position=player.position,
        lineup_slot=player.lineup_slot,
        is_starter=player.is_starter,
        has_projection=player.has_projection,
        mean=player.mean,
        sd=player.sd,
        availability=player.availability,
        floor=player.floor,
        ceiling=player.ceiling,
    )


def _to_team_roster_out(team: TeamRosterView) -> TeamRosterOut:
    return TeamRosterOut(
        team_id=team.team_id,
        team_name=team.team_name,
        starters=[_to_roster_player_out(p) for p in team.starters],
        bench=[_to_roster_player_out(p) for p in team.bench],
        risk_rating=team.risk_rating,
        positional_concentration=list(team.positional_concentration),
    )


def _to_matchup_out(matchup: ScheduledMatchup) -> ScheduledMatchupOut:
    return ScheduledMatchupOut(
        week=matchup.week,
        home_team_id=matchup.home_team_id,
        home_team_name=matchup.home_team_name,
        away_team_id=matchup.away_team_id,
        away_team_name=matchup.away_team_name,
        winner=matchup.winner,
    )


def _to_season_replay_team_out(team: TeamSeasonReplay) -> SeasonReplayTeamOut:
    return SeasonReplayTeamOut(
        team_id=team.team_id,
        team_name=team.team_name,
        actual_wins=team.actual_wins,
        actual_losses=team.actual_losses,
        actual_ties=team.actual_ties,
        optimal_wins=team.optimal_wins,
        optimal_losses=team.optimal_losses,
        optimal_ties=team.optimal_ties,
        actual_points_for=team.actual_points_for,
        optimal_points_for=team.optimal_points_for,
        neutral_expected_wins=team.neutral_expected_wins,
        neutral_expected_losses=team.neutral_expected_losses,
        neutral_expected_ties=team.neutral_expected_ties,
    )


def _to_draft_pick_grade_out(pick: DraftPickGrade) -> DraftPickGradeOut:
    return DraftPickGradeOut(
        overall_pick_number=pick.overall_pick_number,
        round_id=pick.round_id,
        round_pick_number=pick.round_pick_number,
        team_id=pick.team_id,
        team_name=pick.team_name,
        player_id=pick.player_id,
        player_name=pick.player_name,
        position=pick.position,
        slot_label=pick.slot_label,
        grade_bucket=pick.grade_bucket,
        player_rank=pick.player_rank,
        player_adp=pick.player_adp,
        alternative_player_id=pick.alternative_player_id,
        alternative_player_name=pick.alternative_player_name,
        alternative_player_rank=pick.alternative_player_rank,
        value_gap=pick.value_gap,
        best_overall_available_player_id=pick.best_overall_available_player_id,
        best_overall_available_player_name=pick.best_overall_available_player_name,
        best_overall_available_rank=pick.best_overall_available_rank,
    )


def _to_position_grade_out(grade: PositionGrade) -> PositionGradeOut:
    return PositionGradeOut(
        position=grade.position,
        pick_count=grade.pick_count,
        avg_value_gap=grade.avg_value_gap,
        league_avg_value_gap=grade.league_avg_value_gap,
        label=grade.label,
    )


def _to_position_timing_out(timing: PositionTiming) -> PositionTimingOut:
    return PositionTimingOut(
        position=timing.position,
        team_first_pick_number=timing.team_first_pick_number,
        team_first_pick_round=timing.team_first_pick_round,
        league_avg_first_pick_number=timing.league_avg_first_pick_number,
        team_pick_count=timing.team_pick_count,
        team_avg_value_gap=timing.team_avg_value_gap,
        league_avg_value_gap=timing.league_avg_value_gap,
    )


def _to_team_draft_autopsy_out(team: TeamDraftAutopsy) -> TeamDraftAutopsyOut:
    return TeamDraftAutopsyOut(
        team_id=team.team_id,
        team_name=team.team_name,
        picks=[_to_draft_pick_grade_out(p) for p in team.picks],
        best_pick=_to_draft_pick_grade_out(team.best_pick),
        worst_pick=_to_draft_pick_grade_out(team.worst_pick),
        position_grades=[_to_position_grade_out(g) for g in team.position_grades],
        position_timing=[_to_position_timing_out(t) for t in team.position_timing],
        structural_finding=team.structural_finding,
    )


def _to_draft_autopsy_response(autopsy: DraftAutopsy) -> DraftAutopsyResponse:
    return DraftAutopsyResponse(
        league_id=autopsy.league_id,
        season_id=autopsy.season_id,
        rank_source=autopsy.rank_source,
        teams=[_to_team_draft_autopsy_out(t) for t in autopsy.teams],
    )


def _to_slot_playoff_strength_out(slot: SlotPlayoffStrength) -> SlotPlayoffStrengthOut:
    return SlotPlayoffStrengthOut(
        slot_label=slot.slot_label,
        regular_mean_points_per_week=slot.regular_mean_points_per_week,
        playoff_mean_points_per_week=slot.playoff_mean_points_per_week,
        regular_floor_points_per_week=slot.regular_floor_points_per_week,
        playoff_floor_points_per_week=slot.playoff_floor_points_per_week,
        regular_floor_ratio=slot.regular_floor_ratio,
        playoff_floor_ratio=slot.playoff_floor_ratio,
        floor_ratio_delta=slot.floor_ratio_delta,
        has_bench_depth=slot.has_bench_depth,
        league_rank=slot.league_rank,
        league_team_count=slot.league_team_count,
        league_percentile=slot.league_percentile,
        is_playoff_specific_weakness=slot.is_playoff_specific_weakness,
    )


def _to_seed_odds_out(seeding: PlayoffSeedOdds) -> PlayoffSeedOddsOut:
    return PlayoffSeedOddsOut(
        team_id=seeding.team_id,
        team_name=seeding.team_name,
        title_probability=seeding.title_probability,
        playoff_probability=seeding.playoff_probability,
        reached_final_probability=seeding.reached_final_probability,
        finish_distribution=list(seeding.finish_distribution),
        seed_probabilities=list(seeding.seed_probabilities),
        projected_seed=seeding.projected_seed,
    )


def _to_bracket_matchup_out(matchup: BracketMatchup) -> BracketMatchupOut:
    return BracketMatchupOut(
        high_seed=matchup.high_seed,
        high_seed_team_id=matchup.high_seed_team_id,
        high_seed_team_name=matchup.high_seed_team_name,
        low_seed=matchup.low_seed,
        low_seed_team_id=matchup.low_seed_team_id,
        low_seed_team_name=matchup.low_seed_team_name,
    )


def _to_team_playoff_plan_out(team: TeamPlayoffPlan) -> TeamPlayoffPlanOut:
    return TeamPlayoffPlanOut(
        team_id=team.team_id,
        team_name=team.team_name,
        slot_strengths=[_to_slot_playoff_strength_out(s) for s in team.slot_strengths],
        weakest_slot=team.weakest_slot,
        recommendation=team.recommendation,
    )


def _to_playoff_planner_response(planner: PlayoffPlannerResult) -> PlayoffPlannerResponse:
    return PlayoffPlannerResponse(
        league_id=planner.league_id,
        season_id=planner.season_id,
        n_regular_weeks=planner.n_regular_weeks,
        n_playoff_rounds=planner.n_playoff_rounds,
        n_playoff_teams=planner.n_playoff_teams,
        seed=planner.seed,
        n_sims=planner.n_sims,
        seeding=[_to_seed_odds_out(s) for s in planner.seeding],
        bracket=[_to_bracket_matchup_out(b) for b in planner.bracket],
        teams=[_to_team_playoff_plan_out(t) for t in planner.teams],
    )


def _to_lineup_slot_assignment_out(assignment: LineupSlotAssignment) -> LineupSlotAssignmentOut:
    return LineupSlotAssignmentOut(
        slot_label=assignment.slot_label,
        player_id=assignment.player_id,
        player_name=assignment.player_name,
        position=assignment.position,
        is_swap=assignment.is_swap,
    )


def _to_lineup_projection_out(projection: LineupProjection) -> LineupProjectionOut:
    return LineupProjectionOut(
        label=projection.label,
        assignments=[_to_lineup_slot_assignment_out(a) for a in projection.assignments],
        weekly_mean=projection.weekly_mean,
        weekly_floor=projection.weekly_floor,
        weekly_ceiling=projection.weekly_ceiling,
        title_probability=projection.title_probability,
        playoff_probability=projection.playoff_probability,
        finish_distribution=list(projection.finish_distribution),
    )


def _to_lineup_optimizer_response(result: LineupOptimizerResult) -> LineupOptimizerResponse:
    return LineupOptimizerResponse(
        league_id=result.league_id,
        season_id=result.season_id,
        team_id=result.team_id,
        team_name=result.team_name,
        seed=result.seed,
        weekly_n_sims=result.weekly_n_sims,
        season_n_sims=result.season_n_sims,
        n_candidates_considered=result.n_candidates_considered,
        current=_to_lineup_projection_out(result.current),
        safest=_to_lineup_projection_out(result.safest),
        highest_upside=_to_lineup_projection_out(result.highest_upside),
    )


def _to_response(
    league_id: int,
    season_id: int,
    seed: int,
    computed_at: datetime,
    result_dict: dict[str, Any],
) -> SimulationResponse:
    return SimulationResponse(
        league_id=league_id,
        season_id=season_id,
        n_sims=result_dict["n_sims"],
        seed=seed,
        computed_at=computed_at,
        teams=[TeamOutcome(**t) for t in result_dict["teams"]],
    )


_scheduler: Any = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global _scheduler
    _scheduler = start_scheduler()
    try:
        yield
    finally:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            _scheduler = None


app = FastAPI(title="fantavo sim API", lifespan=lifespan)


def get_dsn() -> str:
    return dsn_from_env("DATABASE_URL", DEFAULT_DEV_DSN)


def get_connection(dsn: str = Depends(get_dsn)) -> Iterator[psycopg.Connection[Any]]:
    conn = connect(dsn)
    try:
        yield conn
    finally:
        conn.close()


@app.get("/league/{league_id}/simulation", response_model=SimulationResponse)
def get_simulation(
    league_id: int,
    season_id: int | None = None,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> SimulationResponse:
    try:
        resolved_season_id = resolve_season_id(conn, league_id, season_id)
    except LeagueNotIngestedError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    cached = read_cached_simulation(conn, league_id, resolved_season_id)
    if cached is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no precomputed simulation cached for league_id={league_id} "
                f"season_id={resolved_season_id} yet -- the scheduled precompute job "
                "has not run for this league (it may not have a drafted roster yet)"
            ),
        )
    return _to_response(
        league_id, resolved_season_id, cached.seed, cached.computed_at, cached.result
    )


@app.post("/league/{league_id}/whatif", response_model=SimulationResponse)
def post_whatif(
    league_id: int,
    req: WhatIfRequest,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> SimulationResponse:
    try:
        resolved_season_id = resolve_season_id(conn, league_id, req.season_id)
        loaded = load_league(conn, league_id, resolved_season_id)
    except LeagueNotIngestedError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except _DATA_UNAVAILABLE_ERRORS as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    overrides: dict[int, tuple[PlayerParams, ...]] = {}
    for team_id, player_ids in req.roster_overrides.items():
        starters: list[PlayerParams] = []
        for player_id in player_ids:
            params = loaded.players_by_id.get(player_id)
            if params is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"roster_overrides references player_id={player_id} for "
                        f"team_id={team_id}, which has no ingested projection for "
                        f"league_id={league_id} season_id={resolved_season_id} -- "
                        "refusing to fabricate one"
                    ),
                )
            starters.append(params)
        overrides[team_id] = tuple(starters)

    seed = req.seed if req.seed is not None else draw_whatif_seed()
    rng = np.random.default_rng(seed)
    try:
        result = simulate_seasons(
            loaded.league,
            n_sims=req.n_sims,
            rng=rng,
            roster_overrides=overrides or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    computed_at = datetime.now(timezone.utc)
    return _to_response(
        league_id, resolved_season_id, seed, computed_at, serialize_result(result)
    )


@app.get("/league/{league_id}/roster", response_model=RosterResponse)
def get_roster(
    league_id: int,
    season_id: int | None = None,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> RosterResponse:
    try:
        resolved_season_id = resolve_season_id(conn, league_id, season_id)
        rosters = load_team_rosters(conn, league_id, resolved_season_id)
    except LeagueNotIngestedError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except _DATA_UNAVAILABLE_ERRORS as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return RosterResponse(
        league_id=league_id,
        season_id=resolved_season_id,
        teams=[_to_team_roster_out(t) for t in rosters],
    )


@app.get("/league/{league_id}/schedule", response_model=ScheduleResponse)
def get_schedule(
    league_id: int,
    season_id: int | None = None,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> ScheduleResponse:
    try:
        resolved_season_id = resolve_season_id(conn, league_id, season_id)
        schedule = load_schedule(conn, league_id, resolved_season_id)
    except LeagueNotIngestedError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return ScheduleResponse(
        league_id=league_id,
        season_id=resolved_season_id,
        n_regular_weeks=schedule.n_regular_weeks,
        current_week=schedule.current_week,
        weeks=[[_to_matchup_out(m) for m in week] for week in schedule.weeks],
    )


@app.post("/league/{league_id}/whatif/season-replay", response_model=SeasonReplayResponse)
def post_season_replay(
    league_id: int,
    req: SeasonReplayRequest,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> SeasonReplayResponse:
    """Alternate-lineup and schedule-neutrality what-ifs -- see
    sim.api.season_replay_view's module docstring for what "actual" means
    here (one sampled realization, clearly labeled, never a real result).
    """
    try:
        resolved_season_id = resolve_season_id(conn, league_id, req.season_id)
    except LeagueNotIngestedError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    seed = req.seed if req.seed is not None else draw_whatif_seed()
    rng = np.random.default_rng(seed)
    try:
        result = compute_season_replay(conn, league_id, resolved_season_id, rng=rng)
    except _DATA_UNAVAILABLE_ERRORS as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return SeasonReplayResponse(
        league_id=league_id,
        season_id=resolved_season_id,
        n_regular_weeks=result.n_regular_weeks,
        seed=seed,
        teams=[_to_season_replay_team_out(t) for t in result.teams],
    )


@app.get("/league/{league_id}/draft-autopsy", response_model=DraftAutopsyResponse)
def get_draft_autopsy(
    league_id: int,
    season_id: int | None = None,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> DraftAutopsyResponse:
    """Per-pick draft grading -- see sim.api.draft_autopsy_view's module
    docstring for the full methodology and the rank-source/data-provenance
    reasoning. Raises 409 for a league with no completed draft to grade
    (a pre-draft league, or the SYNTHETIC validation league, whose mock
    draft never records a real pick sequence)."""
    try:
        resolved_season_id = resolve_season_id(conn, league_id, season_id)
        autopsy = compute_draft_autopsy(conn, league_id, resolved_season_id)
    except LeagueNotIngestedError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except _DATA_UNAVAILABLE_ERRORS as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _to_draft_autopsy_response(autopsy)


@app.get("/league/{league_id}/playoff-planner", response_model=PlayoffPlannerResponse)
def get_playoff_planner(
    league_id: int,
    season_id: int | None = None,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> PlayoffPlannerResponse:
    """Projected playoff bracket, seeding odds, and per-roster-slot playoff-
    window strength -- see `sim.api.playoff_planner_view`'s module docstring
    for the full methodology, including why this reuses
    `sim.engine.simulate_seasons()` and `sim.engine._sample_player_weeks`
    unmodified rather than adding a second simulation path, and how it
    resolves PLAN.md's "strength of schedule" phrase for a fixture with no
    real NFL opponent data."""
    try:
        resolved_season_id = resolve_season_id(conn, league_id, season_id)
        planner = compute_playoff_planner(conn, league_id, resolved_season_id)
    except LeagueNotIngestedError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except _DATA_UNAVAILABLE_ERRORS as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _to_playoff_planner_response(planner)


@app.get(
    "/league/{league_id}/lineup-optimizer/{team_id}",
    response_model=LineupOptimizerResponse,
)
def get_lineup_optimizer(
    league_id: int,
    team_id: int,
    season_id: int | None = None,
    seed: int | None = None,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> LineupOptimizerResponse:
    """Current / safest / highest-upside lineups for one team -- see
    `sim.api.lineup_optimizer_view`'s module docstring for the full
    methodology: why floor is derived from real Monte Carlo samples of the
    team total (never a sum of individual player floors), why
    "highest upside" means season title probability from a real
    `simulate_seasons()` call (never mean points), and exactly what search
    space ("every single-slot swap") makes re-simulating that tractable.
    Raises 404 for an unknown team_id, exactly like an un-ingested league."""
    try:
        resolved_season_id = resolve_season_id(conn, league_id, season_id)
        result = compute_lineup_optimizer(
            conn, league_id, resolved_season_id, team_id, seed=seed
        )
    except LeagueNotIngestedError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UnknownTeamError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except _DATA_UNAVAILABLE_ERRORS as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _to_lineup_optimizer_response(result)
