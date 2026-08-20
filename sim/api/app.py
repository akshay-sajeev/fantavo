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
    GET  /league/{league_id}/waiver-intelligence/{team_id}
                                           ranked free-agent priority list, per team
    GET  /league/{league_id}/power-ranking-roast
                                           good-natured, fact-grounded roast per team
    POST /league/{league_id}/analyst/{team_id}
                                           AI league analyst -- Gemini tool-calling
                                           over the six routes above (see
                                           sim.api.analyst_view / sim.api.analyst_tools)

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
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from ingest.db import DEFAULT_DEV_DSN, connect, dsn_from_env
from ingest.errors import IngestError
from ingest.espn_client import EspnFetchError
from sim.api import auth_view, league_connection_view
from sim.api.analyst_tools import UnknownAnalystTeamError
from sim.api.analyst_view import (
    AnalystConfigError,
    AnalystMessage,
    AnalystTurnResult,
    run_analyst_turn,
)
from sim.api.beat_my_league_view import (
    BeatMyLeagueResult,
    RivalThreat,
    TeamAdvantage,
    TeamLeagueProfile,
    TradeCaution,
    compute_beat_my_league,
)
from sim.api.beat_my_league_view import UnknownTeamError as BeatMyLeagueUnknownTeamError
from sim.api.cache import read_cached_simulation, serialize_result
from sim.api.crypto import CredentialEncryptionError
from sim.api.draft_autopsy_view import (
    DraftAutopsy,
    DraftPickGrade,
    PositionGrade,
    PositionTiming,
    TeamDraftAutopsy,
    compute_draft_autopsy,
)
from sim.api.env import load_dotenv_once
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
from sim.api.precompute import precompute_all_leagues, precompute_league
from sim.api.reingest import reingest_all_connected_users, reingest_user
from sim.api.roast_view import (
    PowerRankingRoastResult,
    RoastFact,
    TeamRoast,
    compute_power_ranking_roast,
)
from sim.api.roster_view import RosterPlayer, TeamRosterView, load_team_rosters
from sim.api.schedule_view import ScheduledMatchup, load_schedule
from sim.api.scheduler import start_scheduler
from sim.api.season_replay_view import TeamSeasonReplay, compute_season_replay
from sim.api.seeds import draw_whatif_seed
from sim.api.waiver_intelligence_view import (
    DEFAULT_LIMIT_PER_POSITION as WAIVER_DEFAULT_LIMIT_PER_POSITION,
)
from sim.api.waiver_intelligence_view import UnknownTeamError as WaiverUnknownTeamError
from sim.api.waiver_intelligence_view import (
    WaiverCandidate,
    WaiverIntelligenceResult,
    WaiverPositionGroup,
    compute_waiver_intelligence,
)
from sim.engine import PlayerParams, simulate_seasons
from sim.params.errors import ParamsError

# So `uvicorn sim.api.app:app` can pick up GEMINI_API_KEY (and any future
# .env-only setting) from the repo-root .env without the caller having to
# `export` it manually first -- see sim.api.env's docstring. Non-destructive
# (setdefault only) and called once, at import time, before any request
# (including the analyst route) can run.
load_dotenv_once()

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
    bench_depth_relevant: bool
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


class WaiverCandidateOut(BaseModel):
    """One free agent, scored on Opportunity/Availability (Signals 1-2) for
    one specific requesting team -- see `sim.api.waiver_intelligence_view`'s
    module docstring for the exact derivation and meaning of every field.
    League fit/Competition (Signals 3-4) live on the enclosing
    `WaiverPositionGroupOut` instead, since they're position-level facts,
    not player-level ones."""

    player_id: int
    player_name: str
    injury_status: str | None
    mean_points_per_game: float
    season_availability: float
    percent_owned: float
    percent_started: float
    percent_change: float
    average_draft_position: float | None
    start_rate_ratio: float
    opportunity_score: float
    expected_playable_points: float
    reasoning: str


class WaiverPositionGroupOut(BaseModel):
    """One position's worth of ranked free-agent candidates for one specific
    requesting team, plus the League fit / Competition facts (Signals 3-4)
    -- see `sim.api.waiver_intelligence_view`'s module docstring, especially
    "Why this is grouped by position, not one flat list"."""

    position: str
    bench_depth_relevant: bool
    team_bench_depth_at_position: int
    team_has_positional_need: bool
    team_starters_at_position: list[str]
    rival_teams_with_need: list[str]
    group_reasoning: str
    candidates: list[WaiverCandidateOut]


class WaiverIntelligenceResponse(BaseModel):
    league_id: int
    season_id: int
    team_id: int
    team_name: str
    ownership_data_note: str
    groups: list[WaiverPositionGroupOut]


class TeamLeagueProfileOut(BaseModel):
    """One team's league-wide standing -- see
    `sim.api.beat_my_league_view.TeamLeagueProfile` for exactly what
    `strengths`/`weaknesses` are (a pure selection over Playoff Planner's own
    already-computed `SlotPlayoffStrength` list, not a new formula) and what
    `playoff_schedule_note` is (Playoff Planner's own recommendation,
    surfaced verbatim)."""

    team_id: int
    team_name: str
    title_probability: float
    playoff_probability: float
    finish_distribution: list[float]
    strengths: list[SlotPlayoffStrengthOut]
    weaknesses: list[SlotPlayoffStrengthOut]
    playoff_schedule_note: str


class RivalThreatOut(BaseModel):
    """See `sim.api.beat_my_league_view`'s module docstring for exactly how
    the biggest threat is selected and why `overlapping_slots` can be
    empty (an honest fallback, not a bug)."""

    team_id: int
    team_name: str
    title_probability: float
    overlapping_slots: list[str]
    reasoning: str


class TeamAdvantageOut(BaseModel):
    slots: list[str]
    reasoning: str


class TradeCautionOut(BaseModel):
    position: str
    team_bench_depth_at_position: int
    bench_player_names: list[str]
    rival_teams_with_need: list[str]
    reasoning: str


class BeatMyLeagueResponse(BaseModel):
    league_id: int
    season_id: int
    team_id: int
    team_name: str
    seed: int
    n_sims: int
    teams: list[TeamLeagueProfileOut]
    biggest_threat: RivalThreatOut
    real_advantage: TeamAdvantageOut
    # A real, honest empty list for a team with no positional trade leverage
    # anywhere in the league right now -- see module docstring.
    trade_cautions: list[TradeCautionOut]


class RoastFactOut(BaseModel):
    """One concrete fact backing one sentence of a roast -- see
    `sim.api.roast_view.RoastFact`. `kind` lets the UI badge/group facts;
    `text` is the short, real citation (a rank, a player name, a percentage)
    the joke next to it is grounded in."""

    kind: str
    text: str


class TeamRoastOut(BaseModel):
    team_id: int
    team_name: str
    title_probability: float
    title_rank: int
    league_team_count: int
    roast: str
    facts: list[RoastFactOut]


class PowerRankingRoastResponse(BaseModel):
    league_id: int
    season_id: int
    seed: int
    n_sims: int
    # False for a league with no completed draft to grade (e.g. the
    # SYNTHETIC validation league) -- every roast is still real, just
    # without a draft-derived sentence. See sim.api.roast_view's docstring.
    has_draft_data: bool
    # Ordered by title_probability descending -- the power-ranking order.
    teams: list[TeamRoastOut]


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
        bench_depth_relevant=slot.bench_depth_relevant,
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


def _to_waiver_candidate_out(candidate: WaiverCandidate) -> WaiverCandidateOut:
    return WaiverCandidateOut(
        player_id=candidate.player_id,
        player_name=candidate.player_name,
        injury_status=candidate.injury_status,
        mean_points_per_game=candidate.mean_points_per_game,
        season_availability=candidate.season_availability,
        percent_owned=candidate.percent_owned,
        percent_started=candidate.percent_started,
        percent_change=candidate.percent_change,
        average_draft_position=candidate.average_draft_position,
        start_rate_ratio=candidate.start_rate_ratio,
        opportunity_score=candidate.opportunity_score,
        expected_playable_points=candidate.expected_playable_points,
        reasoning=candidate.reasoning,
    )


def _to_waiver_position_group_out(group: WaiverPositionGroup) -> WaiverPositionGroupOut:
    return WaiverPositionGroupOut(
        position=group.position,
        bench_depth_relevant=group.bench_depth_relevant,
        team_bench_depth_at_position=group.team_bench_depth_at_position,
        team_has_positional_need=group.team_has_positional_need,
        team_starters_at_position=list(group.team_starters_at_position),
        rival_teams_with_need=list(group.rival_teams_with_need),
        group_reasoning=group.group_reasoning,
        candidates=[_to_waiver_candidate_out(c) for c in group.candidates],
    )


def _to_waiver_intelligence_response(
    result: WaiverIntelligenceResult,
) -> WaiverIntelligenceResponse:
    return WaiverIntelligenceResponse(
        league_id=result.league_id,
        season_id=result.season_id,
        team_id=result.team_id,
        team_name=result.team_name,
        ownership_data_note=result.ownership_data_note,
        groups=[_to_waiver_position_group_out(g) for g in result.groups],
    )


def _to_team_league_profile_out(profile: TeamLeagueProfile) -> TeamLeagueProfileOut:
    return TeamLeagueProfileOut(
        team_id=profile.team_id,
        team_name=profile.team_name,
        title_probability=profile.title_probability,
        playoff_probability=profile.playoff_probability,
        finish_distribution=list(profile.finish_distribution),
        strengths=[_to_slot_playoff_strength_out(s) for s in profile.strengths],
        weaknesses=[_to_slot_playoff_strength_out(s) for s in profile.weaknesses],
        playoff_schedule_note=profile.playoff_schedule_note,
    )


def _to_rival_threat_out(threat: RivalThreat) -> RivalThreatOut:
    return RivalThreatOut(
        team_id=threat.team_id,
        team_name=threat.team_name,
        title_probability=threat.title_probability,
        overlapping_slots=list(threat.overlapping_slots),
        reasoning=threat.reasoning,
    )


def _to_team_advantage_out(advantage: TeamAdvantage) -> TeamAdvantageOut:
    return TeamAdvantageOut(slots=list(advantage.slots), reasoning=advantage.reasoning)


def _to_trade_caution_out(caution: TradeCaution) -> TradeCautionOut:
    return TradeCautionOut(
        position=caution.position,
        team_bench_depth_at_position=caution.team_bench_depth_at_position,
        bench_player_names=list(caution.bench_player_names),
        rival_teams_with_need=list(caution.rival_teams_with_need),
        reasoning=caution.reasoning,
    )


def _to_beat_my_league_response(result: BeatMyLeagueResult) -> BeatMyLeagueResponse:
    return BeatMyLeagueResponse(
        league_id=result.league_id,
        season_id=result.season_id,
        team_id=result.team_id,
        team_name=result.team_name,
        seed=result.seed,
        n_sims=result.n_sims,
        teams=[_to_team_league_profile_out(t) for t in result.teams],
        biggest_threat=_to_rival_threat_out(result.biggest_threat),
        real_advantage=_to_team_advantage_out(result.real_advantage),
        trade_cautions=[_to_trade_caution_out(c) for c in result.trade_cautions],
    )


def _to_roast_fact_out(fact: RoastFact) -> RoastFactOut:
    return RoastFactOut(kind=fact.kind, text=fact.text)


def _to_team_roast_out(team: TeamRoast) -> TeamRoastOut:
    return TeamRoastOut(
        team_id=team.team_id,
        team_name=team.team_name,
        title_probability=team.title_probability,
        title_rank=team.title_rank,
        league_team_count=team.league_team_count,
        roast=team.roast,
        facts=[_to_roast_fact_out(f) for f in team.facts],
    )


class AnalystMessageIn(BaseModel):
    """One turn of the persisted chat transcript, oldest first, ending in
    the newest user message. This service keeps no session state -- the
    frontend resends the full transcript on every request, the same
    no-auth/no-session-store convention the rest of this app already uses
    (see sim.api.analyst_view.AnalystMessage's docstring)."""

    role: str  # "user" | "model"
    text: str


class AnalystChatRequest(BaseModel):
    season_id: int | None = None
    messages: list[AnalystMessageIn] = Field(default_factory=list)


class AnalystCitationOut(BaseModel):
    """One real numeric fact extracted verbatim from a tool result this
    turn -- see sim.api.analyst_view.Citation. `percent` is always on a
    0-100 scale so the frontend can render every citation the same way
    regardless of which tool it came from."""

    index: int
    source_tool: str
    kind: str
    subject: str
    percent: float
    display: str


class AnalystSpanOut(BaseModel):
    """A character range in `reply` where a percentage the model actually
    wrote was matched (within rounding tolerance) against a real citation's
    value -- see sim.api.analyst_view.CitationSpan and its module docstring
    for the exact matching rule. The frontend renders this exact range as a
    <StatChip> instead of plain text; nothing here is computed client-side."""

    start: int
    end: int
    citation_index: int


class AnalystToolCallOut(BaseModel):
    """One real tool invocation made this turn, verbatim -- name, the
    arguments Gemini supplied, and the real result
    `sim.api.analyst_tools.TOOL_REGISTRY` returned. Kept in the response so
    every number in `reply` can be cross-checked against the exact tool
    call it came from (this is also how this phase's own manual
    verification step confirms the model's numbers match the real
    endpoints -- see docs/decisions.md Phase 13)."""

    name: str
    args: dict[str, Any]
    result: dict[str, Any]


class AnalystChatResponse(BaseModel):
    league_id: int
    season_id: int
    team_id: int
    team_name: str
    reply: str
    citations: list[AnalystCitationOut]
    spans: list[AnalystSpanOut]
    tool_calls: list[AnalystToolCallOut]


def _to_analyst_chat_response(
    league_id: int, season_id: int, result: AnalystTurnResult
) -> AnalystChatResponse:
    return AnalystChatResponse(
        league_id=league_id,
        season_id=season_id,
        team_id=result.team_id,
        team_name=result.team_name,
        reply=result.reply,
        citations=[
            AnalystCitationOut(
                index=c.index,
                source_tool=c.source_tool,
                kind=c.kind,
                subject=c.subject,
                percent=c.percent,
                display=c.display,
            )
            for c in result.citations
        ],
        spans=[
            AnalystSpanOut(start=s.start, end=s.end, citation_index=s.citation_index)
            for s in result.spans
        ],
        tool_calls=[
            AnalystToolCallOut(name=t.name, args=t.args, result=t.result) for t in result.tool_calls
        ],
    )


def _to_power_ranking_roast_response(result: PowerRankingRoastResult) -> PowerRankingRoastResponse:
    return PowerRankingRoastResponse(
        league_id=result.league_id,
        season_id=result.season_id,
        seed=result.seed,
        n_sims=result.n_sims,
        has_draft_data=result.has_draft_data,
        teams=[_to_team_roast_out(t) for t in result.teams],
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


class SignupRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponseOut(BaseModel):
    token: str
    user_id: int
    email: str


class MeResponseOut(BaseModel):
    user_id: int
    email: str


class ConnectLeagueRequest(BaseModel):
    league_id: int
    espn_s2: str | None = None
    swid: str | None = None


class TeamOptionOut(BaseModel):
    team_id: int
    name: str


class ConnectLeagueResponseOut(BaseModel):
    teams: list[TeamOptionOut]


class SetTeamRequest(BaseModel):
    team_id: int


class LeagueConnectionOut(BaseModel):
    league_id: int | None
    season_id: int | None
    team_id: int | None
    connected_at: datetime | None
    teams: list[TeamOptionOut]


class RefreshLeagueResponse(BaseModel):
    status: str
    ingested_at: datetime | None
    odds_updated: bool


# 5 minutes: long enough that spamming the button can't meaningfully
# stress ESPN's own (undocumented) rate limits, short enough the button
# never feels broken. Keyed on the league, not the user -- this app's
# shared-league-view model means a second connected user's refresh within
# this window would just repeat the same work, not serve a genuinely
# different need. See docs/superpowers/specs/2026-08-19-manual-league-
# refresh-design.md.
REFRESH_COOLDOWN = timedelta(minutes=5)


_scheduler: Any = None


def _should_run_in_process_scheduler() -> bool:
    """False on Vercel (VERCEL=1, set automatically by Vercel's runtime --
    verify this exact variable against Vercel's current docs at deploy
    time, see docs/decisions.md's Vercel Serverless Migration entry): a
    serverless function has no persistent process for an in-process
    APScheduler background thread to survive between invocations, and
    Vercel Cron Jobs calling /internal/precompute and /internal/reingest
    take over that role there instead. True everywhere else (local
    `uvicorn`, or a future non-Vercel host), preserving automatic
    recurring precompute/reingest for local development."""
    return os.environ.get("VERCEL") != "1"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global _scheduler
    if _should_run_in_process_scheduler():
        _scheduler = start_scheduler()
    try:
        yield
    finally:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            _scheduler = None


# No existing convention distinguishes prod from dev on the Python side
# (unlike the web layer's automatic NODE_ENV) -- ENVIRONMENT=production is
# set explicitly on the deployed service. Unset locally, so /docs stays on
# for local development. Gates the interactive API docs, which would
# otherwise publish the full route table unauthenticated on a public host.
_is_production = os.environ.get("ENVIRONMENT") == "production"

app = FastAPI(
    title="fantavo sim API",
    lifespan=lifespan,
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)


def get_dsn() -> str:
    return dsn_from_env("DATABASE_URL", DEFAULT_DEV_DSN)


def get_connection(dsn: str = Depends(get_dsn)) -> Iterator[psycopg.Connection[Any]]:
    """`connect()` returns an autocommit=False connection (see its
    docstring), and every read here previously left that fine for the
    read-only routes above -- nothing needed committing. The 4 /auth/*
    routes are the first writes through this dependency, and they expose a
    real gap: a bare, unwrapped `cur.execute()` read (e.g.
    auth_view._raise_if_locked's SELECT) opens an ambient transaction on
    this connection, so a *later* `with conn.transaction():` write in the
    same request (e.g. auth_view._record_failed_login's INSERT, or
    create_session's own INSERT right after a successful login) becomes a
    SAVEPOINT nested inside that still-open ambient transaction rather than
    a top-level commit -- see psycopg's docs on Connection.transaction()
    nesting. Nothing ever commits that outer transaction, so it silently
    rolls back on conn.close() below, and the write never reaches the
    database even though the route returned 200/201/401/429 as if it had.
    Committing unconditionally in `finally` (so it still runs when a route
    raises HTTPException -- e.g. login's 401 after auth_view already wrote
    the failed-attempt row, or its 429 after the 5th) closes that gap for
    every route, present and future, without changing any read-only
    route's behavior (committing a read-only transaction is a no-op)."""
    conn = connect(dsn)
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()


# The same generic text for every one of: signup with an email that's
# already registered, login with an email that has no account, login with
# the wrong password. None of the three may be distinguishable -- see
# auth_view.EmailAlreadyRegisteredError's docstring.
_GENERIC_AUTH_ERROR = "invalid email or password"


def get_bearer_token(authorization: str | None = Header(default=None)) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or malformed Authorization header")
    return authorization.removeprefix("Bearer ").strip()


def require_user(
    token: str = Depends(get_bearer_token),
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> auth_view.AuthedUser:
    """Validates the bearer token against a real, non-expired session.
    Attached directly to /leagues/* since Phase B; Phase C's
    require_league_owner (below) depends on this and adds per-league
    authorization on top of it for all 12 /league/{league_id}/* routes."""
    try:
        return auth_view.validate_session(conn, token, datetime.now(UTC))
    except auth_view.InvalidSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def require_league_owner(
    league_id: int,
    user: auth_view.AuthedUser = Depends(require_user),  # noqa: B008 (idiomatic FastAPI)
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> auth_view.AuthedUser:
    """Phase C: the authoritative per-league authorization check every
    /league/{league_id}/* route needs now that the sim API is no longer
    guaranteed to be reachable only from the Next.js server. FastAPI
    injects `league_id` from the route's own path parameter -- no caller
    passes it explicitly. Raises 403 (never 404) for any league_id that
    isn't this exact user's connected league, including "no connected
    league at all" (state.league_id is None never equals a real league_id)."""
    state = league_connection_view.get_connection_state(conn, user.user_id)
    if state.league_id != league_id:
        raise HTTPException(status_code=403, detail="not authorized for this league")
    return user


def require_cron_secret(authorization: str | None = Header(default=None)) -> None:
    """Gates the two /internal/* endpoints Vercel Cron Jobs call. Not a
    user-facing auth check (no AuthedUser involved) -- this exists so an
    arbitrary public request can't repeatedly trigger a 10,000-sim Monte
    Carlo precompute run. Verify against CRON_SECRET, an env var only
    Vercel's own Cron trigger and this deployment's own config know."""
    expected = os.environ.get("CRON_SECRET")
    if not expected or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid or missing cron secret")


@app.get("/health")
def health(conn: psycopg.Connection[Any] = Depends(get_connection)) -> dict[str, str]:  # noqa: B008 (idiomatic FastAPI)
    """Unauthenticated on purpose -- a deploy healthcheck has no session to
    present, and every other route requires one as of Phase C. Actually
    exercises the DB connection (not just "the process is alive") so a
    broken DATABASE_URL or an unreachable Postgres shows up as an unhealthy
    deploy, not a silently-degraded one."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/internal/precompute")
def trigger_precompute(
    _: None = Depends(require_cron_secret),
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> dict[str, str]:
    """Vercel Cron Job target replacing sim/api/scheduler.py's in-process
    precompute interval job for a serverless deploy (see docs/decisions.md's
    Vercel Serverless Migration entry) -- calls the exact same function
    local development's in-process scheduler calls, once per invocation."""
    precompute_all_leagues(conn, datetime.now(UTC))
    return {"status": "ok"}


@app.get("/internal/reingest")
def trigger_reingest(
    _: None = Depends(require_cron_secret),
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> dict[str, str]:
    """Vercel Cron Job target replacing sim/api/scheduler.py's in-process
    reingest interval job for a serverless deploy -- see trigger_precompute
    above for the same reasoning."""
    reingest_all_connected_users(conn, datetime.now(UTC))
    return {"status": "ok"}


@app.post("/auth/signup", response_model=AuthResponseOut, status_code=201)
def signup(
    body: SignupRequest,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> AuthResponseOut:
    now = datetime.now(UTC)
    try:
        user = auth_view.create_user(conn, body.email, body.password, now)
    except auth_view.EmailAlreadyRegisteredError as exc:
        raise HTTPException(status_code=400, detail=_GENERIC_AUTH_ERROR) from exc
    except ValueError as exc:
        # A too-short password or malformed email -- safe to show verbatim,
        # neither leaks anything about other accounts.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    token = auth_view.create_session(conn, user, now)
    return AuthResponseOut(token=token, user_id=user.user_id, email=user.email)


@app.post("/auth/login", response_model=AuthResponseOut)
def login(
    body: LoginRequest,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> AuthResponseOut:
    now = datetime.now(UTC)
    try:
        user = auth_view.authenticate_user(conn, body.email, body.password, now)
    except auth_view.AccountLockedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except auth_view.InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=_GENERIC_AUTH_ERROR) from exc
    token = auth_view.create_session(conn, user, now)
    return AuthResponseOut(token=token, user_id=user.user_id, email=user.email)


@app.post("/auth/logout", status_code=204)
def logout(
    token: str = Depends(get_bearer_token),
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> None:
    auth_view.delete_session(conn, token)


@app.get("/auth/me", response_model=MeResponseOut)
def me(user: auth_view.AuthedUser = Depends(require_user)) -> MeResponseOut:  # noqa: B008 (idiomatic FastAPI)
    return MeResponseOut(user_id=user.user_id, email=user.email)


@app.post("/leagues/connect", response_model=ConnectLeagueResponseOut)
def connect_league_route(
    body: ConnectLeagueRequest,
    user: auth_view.AuthedUser = Depends(require_user),  # noqa: B008 (idiomatic FastAPI)
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> ConnectLeagueResponseOut:
    now = datetime.now(UTC)
    try:
        teams = league_connection_view.connect_league(
            conn, user.user_id, body.league_id, body.espn_s2, body.swid, now
        )
    except league_connection_view.LeagueConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CredentialEncryptionError as exc:
        # A server misconfiguration (missing/invalid CREDENTIAL_ENCRYPTION_KEY),
        # not a caller error -- same 500-with-str(exc) shape as
        # AnalystConfigError below. The exception's own message never includes
        # the key or the credential (see sim.api.crypto), so surfacing it is
        # safe and tells the operator exactly what to fix.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ConnectLeagueResponseOut(
        teams=[TeamOptionOut(team_id=t.team_id, name=t.name) for t in teams]
    )


@app.post("/leagues/team", status_code=204)
def set_team_route(
    body: SetTeamRequest,
    user: auth_view.AuthedUser = Depends(require_user),  # noqa: B008 (idiomatic FastAPI)
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> None:
    try:
        league_connection_view.set_team(conn, user.user_id, body.team_id)
    except (
        league_connection_view.UnknownTeamError,
        league_connection_view.NoConnectedLeagueError,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/leagues/me", response_model=LeagueConnectionOut)
def get_leagues_me(
    user: auth_view.AuthedUser = Depends(require_user),  # noqa: B008 (idiomatic FastAPI)
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> LeagueConnectionOut:
    state = league_connection_view.get_connection_state(conn, user.user_id)
    teams: list[TeamOptionOut] = []
    if state.league_id is not None and state.season_id is not None and state.team_id is None:
        teams = [
            TeamOptionOut(team_id=t.team_id, name=t.name)
            for t in league_connection_view.list_teams_for_league(
                conn, state.league_id, state.season_id
            )
        ]
    return LeagueConnectionOut(
        league_id=state.league_id,
        season_id=state.season_id,
        team_id=state.team_id,
        connected_at=state.connected_at,
        teams=teams,
    )


@app.post("/league/{league_id}/refresh", response_model=RefreshLeagueResponse)
def refresh_league(
    league_id: int,
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008 (idiomatic FastAPI)
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> RefreshLeagueResponse:
    """Manual, on-demand counterpart to the daily Cron-triggered
    /internal/reingest + /internal/precompute pair (see docs/decisions.md's
    Vercel Serverless Migration entry) -- re-ingests and recomputes odds
    for exactly the caller's one connected league, not the full batch."""
    now = datetime.now(UTC)
    season_id = league_connection_view.resolve_current_season_id(now)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT ingested_at FROM league WHERE league_id = %s AND season_id = %s",
            (league_id, season_id),
        )
        row = cur.fetchone()
    if row is not None:
        elapsed = now - row[0]
        if elapsed < REFRESH_COOLDOWN:
            retry_after = REFRESH_COOLDOWN - elapsed
            raise HTTPException(
                status_code=429,
                detail="refreshed too recently, try again shortly",
                headers={"Retry-After": str(max(1, int(retry_after.total_seconds())))},
            )

    try:
        reingest_user(conn, _owner.user_id, now)
    except CredentialEncryptionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except EspnFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except IngestError:
        # A parse-time failure inside ingest_league itself (e.g. a
        # malformed/unparseable ESPN payload) -- nothing was ingested, so
        # nothing changed and there's nothing to precompute either. NOT
        # the not-yet-drafted-season case (that one succeeds here and
        # fails in precompute_league below -- see that branch).
        return RefreshLeagueResponse(status="ok", ingested_at=None, odds_updated=False)

    try:
        precompute_league(conn, league_id, season_id, now)
    except (LeagueNotIngestedError, *_DATA_UNAVAILABLE_ERRORS):
        # e.g. RosterNotAvailableError -- a new NFL season that hasn't
        # drafted yet is a legitimate state, not a failure. reingest_user
        # above already succeeded and committed, so ingested_at reflects
        # that; there's just nothing to precompute yet.
        return RefreshLeagueResponse(status="ok", ingested_at=now, odds_updated=False)

    return RefreshLeagueResponse(status="ok", ingested_at=now, odds_updated=True)


@app.get("/league/{league_id}/simulation", response_model=SimulationResponse)
def get_simulation(
    league_id: int,
    season_id: int | None = None,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008 (idiomatic FastAPI)
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
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008 (idiomatic FastAPI)
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

    computed_at = datetime.now(UTC)
    return _to_response(
        league_id, resolved_season_id, seed, computed_at, serialize_result(result)
    )


@app.get("/league/{league_id}/roster", response_model=RosterResponse)
def get_roster(
    league_id: int,
    season_id: int | None = None,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008 (idiomatic FastAPI)
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
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008 (idiomatic FastAPI)
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
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008 (idiomatic FastAPI)
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
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008 (idiomatic FastAPI)
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
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008 (idiomatic FastAPI)
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
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008 (idiomatic FastAPI)
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


@app.get(
    "/league/{league_id}/waiver-intelligence/{team_id}",
    response_model=WaiverIntelligenceResponse,
)
def get_waiver_intelligence(
    league_id: int,
    team_id: int,
    season_id: int | None = None,
    limit_per_position: int = WAIVER_DEFAULT_LIMIT_PER_POSITION,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008 (idiomatic FastAPI)
) -> WaiverIntelligenceResponse:
    """A ranked, position-grouped waiver-wire priority list for one team,
    scored on opportunity / availability / league fit / competition -- see
    `sim.api.waiver_intelligence_view`'s module docstring for the full
    methodology, the four signals' exact derivation, why the response is
    grouped by position rather than one flat cross-position list, and why
    this endpoint calls no simulation at all. Raises 404 for an unknown
    team_id, exactly like the Lineup Optimizer."""
    try:
        resolved_season_id = resolve_season_id(conn, league_id, season_id)
        result = compute_waiver_intelligence(
            conn, league_id, resolved_season_id, team_id, limit_per_position=limit_per_position
        )
    except LeagueNotIngestedError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WaiverUnknownTeamError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except _DATA_UNAVAILABLE_ERRORS as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _to_waiver_intelligence_response(result)


@app.get(
    "/league/{league_id}/beat-my-league/{team_id}",
    response_model=BeatMyLeagueResponse,
)
def get_beat_my_league(
    league_id: int,
    team_id: int,
    season_id: int | None = None,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008 (idiomatic FastAPI)
) -> BeatMyLeagueResponse:
    """Every team's title odds / structural strengths & weaknesses / playoff
    schedule difficulty, plus one selected team's biggest threat, real
    advantage, and which positions not to trade away -- see
    `sim.api.beat_my_league_view`'s module docstring for the full
    methodology and exactly what is reused from Playoff Planner versus
    computed fresh here. Raises 404 for an unknown team_id, exactly like the
    Lineup Optimizer and Waiver Intelligence."""
    try:
        resolved_season_id = resolve_season_id(conn, league_id, season_id)
        result = compute_beat_my_league(conn, league_id, resolved_season_id, team_id)
    except LeagueNotIngestedError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BeatMyLeagueUnknownTeamError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except _DATA_UNAVAILABLE_ERRORS as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _to_beat_my_league_response(result)


@app.get(
    "/league/{league_id}/power-ranking-roast",
    response_model=PowerRankingRoastResponse,
)
def get_power_ranking_roast(
    league_id: int,
    season_id: int | None = None,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008 (idiomatic FastAPI)
) -> PowerRankingRoastResponse:
    """A good-natured, per-team roast grounded entirely in real, already-
    computed facts (simulated title-odds rank, a real draft reach/steal,
    real zero-bench-depth weaknesses, a real rival threat) -- see
    `sim.api.roast_view`'s module docstring for exactly how each fact is
    selected and why this needs only one `simulate_seasons()` call for the
    whole league, not one per team. Unlike `/draft-autopsy`, a league with no
    completed draft does NOT 409 here -- `has_draft_data` is false instead
    and every roast still renders with its other real material."""
    try:
        resolved_season_id = resolve_season_id(conn, league_id, season_id)
        result = compute_power_ranking_roast(conn, league_id, resolved_season_id)
    except LeagueNotIngestedError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except _DATA_UNAVAILABLE_ERRORS as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _to_power_ranking_roast_response(result)


@app.post(
    "/league/{league_id}/analyst/{team_id}",
    response_model=AnalystChatResponse,
)
def post_analyst_chat(
    league_id: int,
    team_id: int,
    req: AnalystChatRequest,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008 (idiomatic FastAPI)
) -> AnalystChatResponse:
    """AI league analyst: a real Gemini tool-calling loop over the six
    routes above -- see `sim.api.analyst_view` (the loop, citation
    matching) and `sim.api.analyst_tools` (the six thin tool wrappers, each
    calling one of this file's other route's own underlying function) for
    the full methodology. The model interprets and narrates; it never
    computes anything itself, and every number in `reply` traces back to a
    real `tool_calls[i].result` field (see `AnalystChatResponse`'s field
    docs). Scoped to one team (`team_id`), the same URL-driven per-team
    pattern every phase since Lineup Optimizer (9a) uses -- this route
    requires the caller to own the league (require_league_owner, added
    above)."""
    try:
        resolved_season_id = resolve_season_id(conn, league_id, req.season_id)
    except LeagueNotIngestedError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    history = [AnalystMessage(role=m.role, text=m.text) for m in req.messages]
    try:
        result = run_analyst_turn(conn, league_id, resolved_season_id, team_id, history)
    except LeagueNotIngestedError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UnknownAnalystTeamError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except _DATA_UNAVAILABLE_ERRORS as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AnalystConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _to_analyst_chat_response(league_id, resolved_season_id, result)
