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
from sim.api.params_loader import LeagueNotIngestedError, load_league, resolve_season_id
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
