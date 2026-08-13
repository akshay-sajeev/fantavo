"""The six tools the AI league analyst (PLAN.md Phase 13) exposes to Gemini.

Every function in `TOOL_REGISTRY` is a THIN wrapper -- it parses/validates
its arguments, calls one already-existing, already-tested `sim.api` view-
module function (the same one its matching HTTP route already calls), and
reshapes that real result into a compact JSON-able dict for the model. No
function here calls `simulate_seasons()` a second, different way, derives a
new statistic, or invents a plausible-looking number -- CLAUDE.md's "one
simulation engine" and "no invented numbers" rules apply here exactly as
they do to `sim/api/app.py`'s route handlers, which is why this module
imports the same `compute_*` / `load_*` functions those handlers use rather
than re-deriving anything.

Mapping (documented again, briefly, at each function -- see
docs/decisions.md Phase 13 for the full reasoning on each choice):

    get_team_odds        -> GET /league/{id}/simulation (sim.api.cache,
                             cached simulate_seasons() output)
    get_trade_impact     -> POST /league/{id}/whatif (sim.engine.simulate_seasons()
                             via roster_overrides, live -- same engine call
                             the Trade Builder UI already makes)
    get_roster_weaknesses -> GET /league/{id}/roster (sim.api.roster_view:
                             risk_rating, positional_concentration, per-player
                             floor/ceiling) -- chosen over Beat My League's
                             playoff-window weakness because it is the more
                             DIRECT, literal answer to "what is my roster's
                             weakness" (roster composition itself, not a
                             derived playoff-specific signal); Beat My
                             League's weakness selection is what backs
                             get_league_threats instead.
    get_waiver_targets   -> GET /league/{id}/waiver-intelligence/{team_id}
                             (sim.api.waiver_intelligence_view)
    get_playoff_outlook  -> GET /league/{id}/playoff-planner
                             (sim.api.playoff_planner_view)
    get_league_threats   -> GET /league/{id}/beat-my-league/{team_id}
                             (sim.api.beat_my_league_view)

Every tool function catches the specific errors its underlying view module
can raise (`ingest.errors.IngestError` / `sim.params.errors.ParamsError`
subclasses, an unknown-team error, an unresolvable team/player name) and
returns `{"error": "..."}` instead of letting an exception propagate -- the
tool-calling loop (`sim.api.analyst_view`) always gets a JSON-able result
back, and that honest error message is exactly what flows back to Gemini as
the `FunctionResponse`, per PLAN.md's explicit instruction: "If a tool call
fails or returns no usable data, the tool result must say so honestly, and
the model's answer must reflect that rather than estimating or guessing."

Team-name resolution (`_resolve_team_id`) is deliberately conservative: an
exact case-insensitive match, then a substring match ONLY if it is unique.
An ambiguous or unmatched name returns an honest error listing the league's
real team names rather than guessing -- the model must never invent which
team_id a name maps to.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import psycopg
from google.genai import types

from ingest.errors import IngestError
from ingest.parse import parse_player_pool, parse_scoring_table, parse_teams
from sim.api.beat_my_league_view import UnknownTeamError as _BeatMyLeagueUnknownTeamError
from sim.api.beat_my_league_view import compute_beat_my_league
from sim.api.cache import read_cached_simulation
from sim.api.params_loader import load_league, load_raw_payload
from sim.api.playoff_planner_view import compute_playoff_planner
from sim.api.roster_view import load_team_rosters
from sim.api.seeds import draw_whatif_seed
from sim.api.waiver_intelligence_view import UnknownTeamError as _WaiverUnknownTeamError
from sim.api.waiver_intelligence_view import compute_waiver_intelligence
from sim.engine import LeagueParams, simulate_seasons
from sim.params.errors import ParamsError

# Every error class a view module this file calls into can legitimately
# raise for "the data isn't there" (as opposed to a programming bug) --
# translated to an honest {"error": ...} tool result, the same class of
# condition sim/api/app.py's route handlers translate to an HTTP 4xx.
_DATA_UNAVAILABLE_ERRORS = (IngestError, ParamsError)

# n_sims for the live get_trade_impact simulate_seasons() calls -- matches
# sim/api/app.py's LIVE_WHATIF_N_SIMS exactly (this tool is the same live
# what-if computation the Trade Builder UI runs, called in-process instead of
# over a second HTTP round trip within the same request).
_TRADE_N_SIMS = 2_000


class UnknownAnalystTeamError(ValueError):
    """Raised when the URL-bound team_id (the conversation's "my team",
    chosen by the frontend's team-switcher, never guessed by the model)
    is not a real team for this league/season. Mapped to HTTP 404 by the
    route handler, the same class of "this specific thing was never real"
    error every other sim.api view module raises for the same situation."""


@dataclass(frozen=True)
class AnalystContext:
    """Everything every tool call in one conversation turn needs, built
    once per request by `build_context` -- never rebuilt per tool call."""

    conn: psycopg.Connection[Any]
    league_id: int
    season_id: int
    team_id: int
    team_name: str
    team_names_by_id: dict[int, str]


def build_context(
    conn: psycopg.Connection[Any], league_id: int, season_id: int, team_id: int
) -> AnalystContext:
    """Raises `params_loader.LeagueNotIngestedError` (via `load_raw_payload`)
    or `UnknownAnalystTeamError` -- both propagated unchanged so the route
    handler maps them to HTTP 404 exactly like every other per-team view."""
    raw = load_raw_payload(conn, league_id, season_id)
    teams = parse_teams(raw)
    names_by_id = {t.team_id: t.name for t in teams}
    if team_id not in names_by_id:
        raise UnknownAnalystTeamError(
            f"team_id={team_id} is not a team in league_id={league_id} season_id={season_id}"
        )
    return AnalystContext(
        conn=conn,
        league_id=league_id,
        season_id=season_id,
        team_id=team_id,
        team_name=names_by_id[team_id],
        team_names_by_id=names_by_id,
    )


def _resolve_team_id(ctx: AnalystContext, name: str | None) -> tuple[int | None, str | None]:
    """Resolve a model-supplied team name to a real team_id. Returns
    `(team_id, error_message)` -- exactly one is non-None. `name=None`
    (or blank) resolves to the conversation's own bound team, never a
    guess. Conservative on purpose: an ambiguous substring match is refused,
    not silently picked, per CLAUDE.md's "no invented numbers" rule applied
    to team identity, not just stats."""
    if name is None or not name.strip():
        return ctx.team_id, None

    needle = name.strip().lower()
    exact = [tid for tid, tname in ctx.team_names_by_id.items() if tname.lower() == needle]
    if len(exact) == 1:
        return exact[0], None

    substring = [
        tid
        for tid, tname in ctx.team_names_by_id.items()
        if needle in tname.lower() or tname.lower() in needle
    ]
    if len(substring) == 1:
        return substring[0], None

    real_names = ", ".join(sorted(ctx.team_names_by_id.values()))
    if len(substring) > 1:
        candidates = ", ".join(ctx.team_names_by_id[tid] for tid in substring)
        return None, f"'{name}' matches more than one real team ({candidates}) -- ask which one."
    return None, f"'{name}' is not a real team in this league. Real team names: {real_names}."


# --------------------------------------------------------------------------
# 1. get_team_odds -> GET /league/{id}/simulation
# --------------------------------------------------------------------------


def tool_get_team_odds(ctx: AnalystContext, *, team_name: str | None = None) -> dict[str, Any]:
    cached = read_cached_simulation(ctx.conn, ctx.league_id, ctx.season_id)
    if cached is None:
        return {
            "error": (
                "No precomputed season simulation is cached for this league yet -- the "
                "scheduled precompute job has not run for it. Cannot answer with real "
                "title/playoff odds."
            )
        }

    _focus_id, resolution_error = _resolve_team_id(ctx, team_name)
    if resolution_error:
        return {"error": resolution_error}

    teams_sorted = sorted(cached.result["teams"], key=lambda t: -t["title_probability"])
    teams_out = [
        {
            "team_id": t["team_id"],
            "team_name": t["team_name"],
            "title_rank": rank,
            "title_probability": t["title_probability"],
            "playoff_probability": t["playoff_probability"],
            "mean_wins": t["mean_wins"],
            "mean_points_for": t["mean_points_for"],
        }
        for rank, t in enumerate(teams_sorted, start=1)
    ]
    return {
        "seed": cached.seed,
        "n_sims": cached.n_sims,
        "league_team_count": len(teams_out),
        "your_team_id": ctx.team_id,
        "your_team_name": ctx.team_name,
        "teams": teams_out,
    }


# --------------------------------------------------------------------------
# 2. get_roster_weaknesses -> GET /league/{id}/roster
# --------------------------------------------------------------------------


def tool_get_roster_weaknesses(
    ctx: AnalystContext, *, team_name: str | None = None
) -> dict[str, Any]:
    focus_id, resolution_error = _resolve_team_id(ctx, team_name)
    if resolution_error:
        return {"error": resolution_error}

    try:
        rosters = load_team_rosters(ctx.conn, ctx.league_id, ctx.season_id)
    except _DATA_UNAVAILABLE_ERRORS as exc:
        return {"error": str(exc)}

    team = next((t for t in rosters if t.team_id == focus_id), None)
    if team is None:
        return {"error": f"no roster found for team_id={focus_id}"}

    return {
        "team_id": team.team_id,
        "team_name": team.team_name,
        "risk_rating": team.risk_rating,
        "positional_concentration": list(team.positional_concentration),
        "starters": [
            {
                "name": p.name,
                "position": p.position,
                "lineup_slot": p.lineup_slot,
                "mean_points_per_game": p.mean,
                "availability": p.availability,
                "floor": p.floor,
                "ceiling": p.ceiling,
            }
            for p in team.starters
        ],
    }


# --------------------------------------------------------------------------
# 3. get_waiver_targets -> GET /league/{id}/waiver-intelligence/{team_id}
# --------------------------------------------------------------------------

_WAIVER_LIMIT_PER_POSITION = 5


def tool_get_waiver_targets(ctx: AnalystContext, *, position: str | None = None) -> dict[str, Any]:
    try:
        result = compute_waiver_intelligence(
            ctx.conn,
            ctx.league_id,
            ctx.season_id,
            ctx.team_id,
            limit_per_position=_WAIVER_LIMIT_PER_POSITION,
        )
    except _WaiverUnknownTeamError as exc:
        return {"error": str(exc)}
    except _DATA_UNAVAILABLE_ERRORS as exc:
        return {"error": str(exc)}

    groups = result.groups
    if position:
        wanted = position.strip().upper()
        groups = tuple(g for g in groups if g.position.upper() == wanted)
        if not groups:
            real_positions = ", ".join(sorted({g.position for g in result.groups}))
            return {
                "error": (
                    f"'{position}' is not a real position group for this league's waiver "
                    f"wire. Real position groups: {real_positions}."
                )
            }

    return {
        "team_id": result.team_id,
        "team_name": result.team_name,
        "ownership_data_note": result.ownership_data_note,
        "groups": [
            {
                "position": g.position,
                "bench_depth_relevant": g.bench_depth_relevant,
                "team_has_positional_need": g.team_has_positional_need,
                "group_reasoning": g.group_reasoning,
                "candidates": [
                    {
                        "player_name": c.player_name,
                        "injury_status": c.injury_status,
                        "mean_points_per_game": c.mean_points_per_game,
                        "percent_owned": c.percent_owned,
                        "opportunity_score": c.opportunity_score,
                        "expected_playable_points": c.expected_playable_points,
                        "reasoning": c.reasoning,
                    }
                    for c in g.candidates
                ],
            }
            for g in groups
        ],
    }


# --------------------------------------------------------------------------
# 4. get_playoff_outlook -> GET /league/{id}/playoff-planner
# --------------------------------------------------------------------------


def tool_get_playoff_outlook(
    ctx: AnalystContext, *, team_name: str | None = None
) -> dict[str, Any]:
    focus_id, resolution_error = _resolve_team_id(ctx, team_name)
    if resolution_error:
        return {"error": resolution_error}

    try:
        planner = compute_playoff_planner(ctx.conn, ctx.league_id, ctx.season_id)
    except _DATA_UNAVAILABLE_ERRORS as exc:
        return {"error": str(exc)}

    plan = next((t for t in planner.teams if t.team_id == focus_id), None)
    seed_odds = next((s for s in planner.seeding if s.team_id == focus_id), None)
    if plan is None or seed_odds is None:
        return {"error": f"no playoff plan found for team_id={focus_id}"}

    return {
        "team_id": plan.team_id,
        "team_name": plan.team_name,
        "n_playoff_teams": planner.n_playoff_teams,
        "n_playoff_rounds": planner.n_playoff_rounds,
        "title_probability": seed_odds.title_probability,
        "playoff_probability": seed_odds.playoff_probability,
        # int(...) is a JSON-safety cast, not a computation: linear_sum_assignment
        # (sim.api.playoff_planner_view) returns numpy.int64 seed indices, which
        # sim/api/app.py's Pydantic response model silently coerces to a plain
        # int on the HTTP path -- this raw-dict tool result skips Pydantic, so
        # the cast has to happen explicitly here or google-genai's own
        # json.dumps() of the tool result fails outright (caught live during
        # this phase's manual verification, see docs/decisions.md Phase 13).
        "projected_seed": int(seed_odds.projected_seed) if seed_odds.projected_seed is not None else None,
        "weakest_slot": plan.weakest_slot,
        "recommendation": plan.recommendation,
        "slot_strengths": [
            {
                "slot_label": s.slot_label,
                "league_rank": s.league_rank,
                "league_team_count": s.league_team_count,
                "league_percentile": s.league_percentile,
                "is_playoff_specific_weakness": s.is_playoff_specific_weakness,
            }
            for s in plan.slot_strengths
        ],
    }


# --------------------------------------------------------------------------
# 5. get_league_threats -> GET /league/{id}/beat-my-league/{team_id}
# --------------------------------------------------------------------------


def tool_get_league_threats(
    ctx: AnalystContext, *, team_name: str | None = None
) -> dict[str, Any]:
    focus_id, resolution_error = _resolve_team_id(ctx, team_name)
    if resolution_error:
        return {"error": resolution_error}
    assert focus_id is not None

    try:
        result = compute_beat_my_league(ctx.conn, ctx.league_id, ctx.season_id, focus_id)
    except _BeatMyLeagueUnknownTeamError as exc:
        return {"error": str(exc)}
    except _DATA_UNAVAILABLE_ERRORS as exc:
        return {"error": str(exc)}

    return {
        "team_id": result.team_id,
        "team_name": result.team_name,
        "biggest_threat": {
            "team_name": result.biggest_threat.team_name,
            "title_probability": result.biggest_threat.title_probability,
            "overlapping_slots": list(result.biggest_threat.overlapping_slots),
            "reasoning": result.biggest_threat.reasoning,
        },
        "real_advantage": {
            "slots": list(result.real_advantage.slots),
            "reasoning": result.real_advantage.reasoning,
        },
        "trade_cautions": [
            {
                "position": c.position,
                "bench_player_names": list(c.bench_player_names),
                "rival_teams_with_need": list(c.rival_teams_with_need),
                "reasoning": c.reasoning,
            }
            for c in result.trade_cautions
        ],
    }


# --------------------------------------------------------------------------
# 6. get_trade_impact -> POST /league/{id}/whatif (live simulate_seasons())
# --------------------------------------------------------------------------


def _team_index(league: LeagueParams, team_id: int) -> int:
    for i, t in enumerate(league.teams):
        if t.team_id == team_id:
            return i
    raise ValueError(f"team_id={team_id} not in league")


def tool_get_trade_impact(
    ctx: AnalystContext,
    *,
    rival_team_name: str,
    give_player_names: list[str] | None = None,
    receive_player_names: list[str] | None = None,
) -> dict[str, Any]:
    give_player_names = give_player_names or []
    receive_player_names = receive_player_names or []

    if not give_player_names or not receive_player_names:
        return {
            "error": (
                "get_trade_impact needs specific real players named on BOTH sides "
                "(give_player_names from your own current starters, "
                "receive_player_names from the rival's current starters) -- it cannot "
                "evaluate a generic 'should I trade with X' question without knowing "
                "which players. If the user didn't name players, use "
                "get_roster_weaknesses / get_league_threats / get_waiver_targets to "
                "find a real, named candidate trade first, then call this tool with "
                "those specific names."
            )
        }
    if len(give_player_names) != len(receive_player_names):
        return {
            "error": (
                f"give_player_names has {len(give_player_names)} name(s) but "
                f"receive_player_names has {len(receive_player_names)} -- this tool "
                "only evaluates equal-count trades, matching the app's Trade Builder "
                "(so the resulting roster stays the same size)."
            )
        }

    rival_id, resolution_error = _resolve_team_id(ctx, rival_team_name)
    if resolution_error:
        return {"error": resolution_error}
    assert rival_id is not None
    if rival_id == ctx.team_id:
        return {"error": "rival_team_name resolved to your own team -- pick a different team."}

    try:
        loaded = load_league(ctx.conn, ctx.league_id, ctx.season_id)
    except _DATA_UNAVAILABLE_ERRORS as exc:
        return {"error": str(exc)}

    raw = load_raw_payload(ctx.conn, ctx.league_id, ctx.season_id)
    scoring_table = parse_scoring_table(raw)
    player_pool, _skipped = parse_player_pool(raw, scoring_table)
    name_to_id: dict[str, int] = {}
    for p in player_pool:
        name_to_id.setdefault(p.name.lower(), p.player_id)

    def _resolve_players(
        names: list[str], team_id: int, side_label: str
    ) -> list[int] | dict[str, Any]:
        current_ids = {pp.player_id for pp in loaded.league.teams[_team_index(loaded.league, team_id)].starters}
        resolved: list[int] = []
        for name in names:
            pid = name_to_id.get(name.strip().lower())
            if pid is None:
                return {"error": f"'{name}' ({side_label}) is not a real, projectable player in this league."}
            if pid not in current_ids:
                team_name = ctx.team_names_by_id.get(team_id, str(team_id))
                return {
                    "error": (
                        f"'{name}' is not currently a real starter for {team_name} -- this "
                        "tool only trades real current starters, never a fabricated roster spot."
                    )
                }
            resolved.append(pid)
        return resolved

    give_ids = _resolve_players(give_player_names, ctx.team_id, "your team")
    if isinstance(give_ids, dict):
        return give_ids
    receive_ids = _resolve_players(receive_player_names, rival_id, "the rival team")
    if isinstance(receive_ids, dict):
        return receive_ids

    my_idx = _team_index(loaded.league, ctx.team_id)
    rival_idx = _team_index(loaded.league, rival_id)
    my_starters = loaded.league.teams[my_idx].starters
    rival_starters = loaded.league.teams[rival_idx].starters

    new_mine = tuple(p for p in my_starters if p.player_id not in give_ids) + tuple(
        loaded.players_by_id[pid] for pid in receive_ids
    )
    new_rival = tuple(p for p in rival_starters if p.player_id not in receive_ids) + tuple(
        loaded.players_by_id[pid] for pid in give_ids
    )

    # Same seed for both calls (common random numbers) so the reported delta
    # reflects only the roster change, not independent sampling noise --
    # the exact pattern web/app/api/league/[leagueId]/whatif-compare/route.ts
    # already established for the Trade Builder UI (Phase 6).
    seed = draw_whatif_seed()
    try:
        before = simulate_seasons(loaded.league, n_sims=_TRADE_N_SIMS, rng=np.random.default_rng(seed))
        after = simulate_seasons(
            loaded.league,
            n_sims=_TRADE_N_SIMS,
            rng=np.random.default_rng(seed),
            roster_overrides={ctx.team_id: new_mine, rival_id: new_rival},
        )
    except ValueError as exc:
        return {"error": str(exc)}

    def _pick(result: Any, team_id: int) -> dict[str, float]:
        idx = result.team_ids.index(team_id)
        return {
            "title_probability": float(result.won_title[idx]),
            "playoff_probability": float(result.made_playoffs[idx]),
        }

    my_before, my_after = _pick(before, ctx.team_id), _pick(after, ctx.team_id)
    rival_before, rival_after = _pick(before, rival_id), _pick(after, rival_id)

    return {
        "seed": seed,
        "n_sims": _TRADE_N_SIMS,
        "your_team_name": ctx.team_name,
        "rival_team_name": ctx.team_names_by_id.get(rival_id, str(rival_id)),
        "you_give": give_player_names,
        "you_receive": receive_player_names,
        "your_team": {
            "title_probability_before": my_before["title_probability"],
            "title_probability_after": my_after["title_probability"],
            "title_probability_delta": my_after["title_probability"] - my_before["title_probability"],
            "playoff_probability_before": my_before["playoff_probability"],
            "playoff_probability_after": my_after["playoff_probability"],
            "playoff_probability_delta": my_after["playoff_probability"] - my_before["playoff_probability"],
        },
        "rival_team": {
            "title_probability_before": rival_before["title_probability"],
            "title_probability_after": rival_after["title_probability"],
            "title_probability_delta": rival_after["title_probability"] - rival_before["title_probability"],
            "playoff_probability_before": rival_before["playoff_probability"],
            "playoff_probability_after": rival_after["playoff_probability"],
            "playoff_probability_delta": rival_after["playoff_probability"] - rival_before["playoff_probability"],
        },
    }


ToolFn = Callable[..., dict[str, Any]]

TOOL_REGISTRY: dict[str, ToolFn] = {
    "get_team_odds": tool_get_team_odds,
    "get_trade_impact": tool_get_trade_impact,
    "get_roster_weaknesses": tool_get_roster_weaknesses,
    "get_waiver_targets": tool_get_waiver_targets,
    "get_playoff_outlook": tool_get_playoff_outlook,
    "get_league_threats": tool_get_league_threats,
}


def _team_name_schema(description: str) -> types.Schema:
    return types.Schema(type=types.Type.STRING, description=description)


# Gemini function-calling schemas for the six tools -- built once at import
# time (pure declarations, no request-specific data). Descriptions are
# written to steer the model toward real, tool-grounded answers: every
# optional team_name param's description explicitly says "omit for your own
# team" so the model doesn't have to invent one, and get_trade_impact's
# description states its hard requirement (named real players on both
# sides) up front rather than letting the model discover it via an error.
TOOL_DECLARATIONS: list[types.FunctionDeclaration] = [
    types.FunctionDeclaration(
        name="get_team_odds",
        description=(
            "Real, simulated (Monte Carlo) championship and playoff odds for every team in "
            "the league, sorted by title probability. Use this to answer 'why am I "
            "projected to lose/win', 'what are my title odds', or to compare teams' "
            "odds. Backed by the cached simulate_seasons() result."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "team_name": _team_name_schema(
                    "A specific team's real name to highlight, or omit entirely to just "
                    "get the full league table (which already includes your own team)."
                ),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="get_trade_impact",
        description=(
            "Evaluates a SPECIFIC proposed trade with a named rival team by re-running "
            "the real season simulation with both rosters swapped, returning real "
            "before/after title and playoff probability for both teams. REQUIRES real "
            "player names on both sides -- cannot evaluate a vague 'should I trade with "
            "X' question with no players named; find real candidate players first "
            "(e.g. via get_roster_weaknesses or get_league_threats) if the user didn't "
            "name any."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "rival_team_name": _team_name_schema("The real name of the rival team to trade with."),
                "give_player_names": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                    description="Real player name(s) from YOUR current starting roster to trade away.",
                ),
                "receive_player_names": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                    description="Real player name(s) from the RIVAL's current starting roster to receive.",
                ),
            },
            required=["rival_team_name", "give_player_names", "receive_player_names"],
        ),
    ),
    types.FunctionDeclaration(
        name="get_roster_weaknesses",
        description=(
            "Real per-player risk (mean points, availability, floor/ceiling) and "
            "positional concentration (which starting positions have zero bench "
            "depth behind them) for a team's actual current roster. Use this to answer "
            "'what is my biggest weakness' or 'is my roster risky'."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "team_name": _team_name_schema(
                    "A specific team's real name, or omit for your own team."
                ),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="get_waiver_targets",
        description=(
            "Real, ranked free-agent priority list for YOUR team, grouped by position, "
            "each with a real reasoning sentence (opportunity, availability, whether "
            "your team or a rival actually needs that position). Use this to answer "
            "'what should I target on waivers'."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "position": _team_name_schema(
                    "Optional real position group to filter to (e.g. 'RB', 'WR', 'TE', "
                    "'QB', 'K', 'D/ST'). Omit to get every position group."
                ),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="get_playoff_outlook",
        description=(
            "Real simulated playoff/title odds, projected seed, and the single real "
            "playoff-window-specific positional weakness (with a concrete recommendation) "
            "for one team. Use this for playoff-related questions or a second angle on "
            "'why am I projected to lose'."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "team_name": _team_name_schema(
                    "A specific team's real name, or omit for your own team."
                ),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="get_league_threats",
        description=(
            "The single real rival team that most threatens a team (a real contender "
            "specifically strong at that team's own real weak spot), that team's own "
            "real structural advantage over that rival, and which positions NOT to "
            "trade away right now. Use this to answer 'who is my biggest threat'."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "team_name": _team_name_schema(
                    "A specific team's real name (whose threat/advantage to analyze), "
                    "or omit for your own team."
                ),
            },
        ),
    ),
]
