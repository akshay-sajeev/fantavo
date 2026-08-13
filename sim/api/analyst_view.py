"""The AI league analyst's tool-calling loop against Gemini (PLAN.md Phase
13, covering PLAN's features 6 and 17).

This module owns exactly two responsibilities, deliberately kept separate
from `sim.api.analyst_tools` (which owns the six real tool implementations):

1. Drive the actual multi-turn Gemini function-calling loop: send the
   conversation, execute whichever of the six real tools
   (`sim.api.analyst_tools.TOOL_REGISTRY`) the model asks for, feed the real
   result back, repeat until the model returns a final text answer (no more
   function calls) or `max_rounds` is hit.

2. A deterministic, testable post-processing pass that turns the tool
   results already gathered during that loop into `Citation` objects (one
   per real numeric fact a tool returned) and `CitationSpan`s (character
   offsets into the model's own final text where one of those citations'
   value actually appears, matched by parsing the percentage the model
   wrote and comparing it -- within a small rounding tolerance -- against
   each citation's real value). The frontend uses `spans` to render a
   `<StatChip>` in place of the plain number, without ever computing
   anything itself (CLAUDE.md: "no analytics logic in components") --
   `spans`/`citations` are just structure over numbers that were already
   real by construction, extracted from real tool JSON, never invented.

The model NEVER computes -- every citation traces back to a field a real
`sim.api` view-module function actually returned this turn (see
`sim.api.analyst_tools`'s own docstring for exactly which endpoint backs
each tool). `_extract_citable_numbers` below is a plain, deterministic
field-extraction function, not a new statistic: it reads already-real
numbers straight off each tool's own response dict.

Why regex/text matching instead of asking the model to emit citation
markers itself: an LLM reliably emitting an exact numeric index token next
to every cited number is not something this codebase can verify or unit
test without a live model call every time. Matching the number the model
actually wrote (a plain percentage, e.g. "22.3%") back against the real
tool-returned values is fully deterministic and testable with zero network
calls -- see `sim/tests/test_api_analyst.py`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, cast

import psycopg
from google import genai
from google.genai import types

from sim.api.analyst_tools import (
    TOOL_DECLARATIONS,
    TOOL_REGISTRY,
    AnalystContext,
    build_context,
)
from sim.api.env import load_dotenv_once

# "gemini-flash-lite-latest" is Google's own stable alias for its current-
# recommended lightweight/economical Gemini model, not a specific dated
# snapshot -- pinning to a dated snapshot is exactly what broke during this
# phase's own live verification (a "gemini-2.5-flash" snapshot returned "no
# longer available to new users" from a freshly-created API key, even
# though it still appeared in models.list()). The alias is Google's own
# forward-compatible answer to model deprecation. The heavier
# "gemini-flash-latest" alias was tried first and worked functionally
# (confirmed live) but was returning real, reproducible 503 "high demand"
# errors during this phase's verification window; "-lite" responded
# quickly and reliably in the same live tests with correct real tool calls
# -- both a cost-conscious choice (PLAN.md's own "be reasonably economical"
# instruction for this feature) and, empirically, the more available one
# right now. Model choice here is not something CLAUDE.md's `rng`-seed
# reproducibility rules apply to -- this can change if Google's own
# recommended alias target changes.
MODEL_NAME = "gemini-flash-lite-latest"

# A safety cap on tool-calling round trips within one turn -- Gemini could
# in principle keep requesting tools indefinitely (e.g. if it keeps hitting
# error results and retrying). Six rounds comfortably covers every real
# question this phase needs to handle (PLAN.md's "Handles" list needs at
# most 1-2 tool calls each), while still bounding worst-case latency/cost.
MAX_TOOL_ROUNDS = 6

# Matches a percentage the model wrote in its own prose, e.g. "22.3%" or
# "22%". Used only to find WHERE a real cited number appears in text that
# already exists -- never to parse a number the model is allowed to invent
# (see module docstring).
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")

# How close (in percentage points) a percentage the model wrote must be to a
# real citation's value to count as "the same number" -- covers the model
# rounding a real value to the nearest whole percent (worst case: 0.5pp off)
# or to one decimal place. Not a fitted value, just a rounding-tolerance
# choice for a deterministic text-matching pass, the same class of
# presentation-layer constant as sim.api.roster_view's 10th/90th percentile
# band.
_MATCH_TOLERANCE_PCT = 0.5


class AnalystConfigError(RuntimeError):
    """`GEMINI_API_KEY` is missing or blank. A configuration problem, not a
    data-availability one -- the route handler maps this to HTTP 500, never
    409 (which means "the league's real data isn't ready yet"). The message
    never includes the key itself, matching CLAUDE.md's secrets rule
    extended to this new secret per this phase's brief."""


@dataclass(frozen=True)
class AnalystMessage:
    """One turn of the persisted chat transcript. The frontend resends the
    full transcript on every request (this service keeps no session state,
    matching the rest of this app's no-auth/no-session-store design) --
    only user/model text turns are persisted; the internal tool-calling
    round trips for a given model turn are never part of `history`."""

    role: str  # "user" | "model"
    text: str


@dataclass(frozen=True)
class ToolCallLog:
    """One real tool invocation made during this turn -- kept in the
    response so the caller (and this phase's own manual verification step)
    can cross-check every cited number against the exact tool result it
    came from."""

    name: str
    args: dict[str, Any]
    result: dict[str, Any]


@dataclass(frozen=True)
class Citation:
    """One real numeric fact, extracted verbatim from a tool result --
    never computed. `percent` is always on a 0-100 scale (a probability
    fraction like 0.223 is multiplied by 100; an already-0-100 ESPN
    ownership figure is used as-is) so every citation can be matched
    against a percentage the model wrote using one consistent scale."""

    index: int
    source_tool: str
    kind: str
    subject: str
    percent: float
    display: str


@dataclass(frozen=True)
class CitationSpan:
    """A character range in `AnalystTurnResult.reply` where `citation_index`
    (into `AnalystTurnResult.citations`) was found to match a percentage the
    model actually wrote -- see module docstring for the matching rule."""

    start: int
    end: int
    citation_index: int


@dataclass(frozen=True)
class AnalystTurnResult:
    team_id: int
    team_name: str
    reply: str
    citations: tuple[Citation, ...]
    spans: tuple[CitationSpan, ...]
    tool_calls: tuple[ToolCallLog, ...]


@lru_cache(maxsize=1)
def _get_client() -> genai.Client:
    """Lazy, memoized -- constructed on first real use, not at import time,
    so importing this module never requires GEMINI_API_KEY to be set (tests
    inject a fake `client` into `run_analyst_turn` instead)."""
    load_dotenv_once()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise AnalystConfigError(
            "GEMINI_API_KEY is not set -- copy .env.example to .env and fill it in. "
            "(This message never includes the key itself.)"
        )
    return genai.Client(api_key=api_key)


def _system_instruction(ctx: AnalystContext) -> str:
    team_list = "\n".join(
        f"- {name} (team_id={tid})" for tid, name in sorted(ctx.team_names_by_id.items())
    )
    return (
        "You are a fantasy football analyst for one specific ESPN fantasy football "
        "league. You have six real tools backed by this league's real Monte Carlo "
        "season simulation and real roster data -- you never compute a probability, "
        "ranking, projection, or any other statistic yourself.\n\n"
        f"This conversation is about: {ctx.team_name} (team_id={ctx.team_id}). "
        f"\"You\"/\"your team\" always means {ctx.team_name} unless the user clearly "
        "names a different team.\n\n"
        f"Real teams in this league:\n{team_list}\n\n"
        "Non-negotiable rules:\n"
        "- Every number in your answer MUST come from a tool result you actually "
        "received in this conversation. Never estimate, guess, round from memory, or "
        "invent a plausible-looking number.\n"
        "- If a tool result contains an \"error\" key, that is a real failure or a "
        "real absence of data -- report that honestly instead of answering anyway.\n"
        "- get_trade_impact requires real player names actually on the two rosters. "
        "If the user asks something like 'should I trade with <team>' without naming "
        "players, either ask which players, or first call get_roster_weaknesses / "
        "get_league_threats / get_waiver_targets to find a real, specifically-named "
        "candidate trade, then call get_trade_impact with those exact names.\n"
        "- State probabilities as plain percentages (e.g. \"22.3%\") so real numbers "
        "read consistently.\n"
        "- Be concise, specific, and use real team/player names rather than vague "
        "references."
    )


def _pct(fraction: float) -> float:
    return fraction * 100.0


def _extract_citable_numbers(tool_name: str, result: dict[str, Any]) -> list[tuple[str, str, float]]:
    """Pull every real, human-meaningful numeric fact out of one tool's
    result -- `(kind, subject, percent_0_to_100)` tuples. Pure field
    extraction, never a computation: everything returned here is a value
    that already existed verbatim in `result` (or, for a probability
    fraction, that same value multiplied by 100 to put every citation on
    one consistent percent scale -- not a new statistic)."""
    if "error" in result:
        return []

    out: list[tuple[str, str, float]] = []

    if tool_name == "get_team_odds":
        for t in result.get("teams", []):
            out.append(("title_probability", t["team_name"], _pct(t["title_probability"])))
            out.append(("playoff_probability", t["team_name"], _pct(t["playoff_probability"])))

    elif tool_name == "get_roster_weaknesses":
        out.append(("risk_rating", result["team_name"], _pct(result["risk_rating"])))
        for p in result.get("starters", []):
            if p.get("availability") is not None:
                out.append(("availability", p["name"], _pct(p["availability"])))

    elif tool_name == "get_waiver_targets":
        for group in result.get("groups", []):
            for c in group.get("candidates", []):
                out.append(("opportunity_score", c["player_name"], _pct(c["opportunity_score"])))
                # percent_owned is already ESPN's own 0-100 figure -- see
                # sim.api.waiver_intelligence_view's docstring -- not a
                # fraction, so it is NOT multiplied by 100 again here.
                out.append(("percent_owned", c["player_name"], c["percent_owned"]))

    elif tool_name == "get_playoff_outlook":
        out.append(("title_probability", result["team_name"], _pct(result["title_probability"])))
        out.append(("playoff_probability", result["team_name"], _pct(result["playoff_probability"])))
        for s in result.get("slot_strengths", []):
            subject = f"{result['team_name']} {s['slot_label']}"
            # league_percentile is already 0-100 by construction (see
            # sim.api.playoff_planner_view.SlotPlayoffStrength).
            out.append((f"{s['slot_label']}_percentile", subject, s["league_percentile"]))

    elif tool_name == "get_league_threats":
        threat = result.get("biggest_threat")
        if threat:
            out.append(("title_probability", threat["team_name"], _pct(threat["title_probability"])))

    elif tool_name == "get_trade_impact":
        for side, label_key in (("your_team", "your_team_name"), ("rival_team", "rival_team_name")):
            team_result = result.get(side)
            if not team_result:
                continue
            label = result.get(label_key, side)
            for key in (
                "title_probability_before",
                "title_probability_after",
                "playoff_probability_before",
                "playoff_probability_after",
            ):
                out.append((key, label, _pct(team_result[key])))

    return out


def _build_citations(tool_calls: list[ToolCallLog]) -> list[Citation]:
    citations: list[Citation] = []
    index = 0
    for call in tool_calls:
        for kind, subject, percent in _extract_citable_numbers(call.name, call.result):
            citations.append(
                Citation(
                    index=index,
                    source_tool=call.name,
                    kind=kind,
                    subject=subject,
                    percent=percent,
                    display=f"{percent:.1f}%",
                )
            )
            index += 1
    return citations


def _match_citations_in_text(text: str, citations: list[Citation]) -> list[CitationSpan]:
    """Deterministic, pure, and fully unit-testable with zero network calls
    -- see module docstring for why this approach was chosen over asking
    the model to emit citation markers itself. For each percentage the
    model actually wrote, finds the real citation whose value is closest
    (within `_MATCH_TOLERANCE_PCT`) and records a span; a percentage with no
    close-enough real citation is left as plain, unlinked text rather than
    forcing a match."""
    spans: list[CitationSpan] = []
    for m in _PERCENT_RE.finditer(text):
        written_value = float(m.group(1))
        best: Citation | None = None
        best_diff = _MATCH_TOLERANCE_PCT
        for citation in citations:
            diff = abs(citation.percent - written_value)
            if diff <= best_diff:
                best = citation
                best_diff = diff
        if best is not None:
            spans.append(CitationSpan(start=m.start(), end=m.end(), citation_index=best.index))
    return spans


def run_analyst_turn(
    conn: psycopg.Connection[Any],
    league_id: int,
    season_id: int,
    team_id: int,
    history: list[AnalystMessage],
    *,
    client: genai.Client | None = None,
    model: str = MODEL_NAME,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> AnalystTurnResult:
    """Run one full model turn: send `history` (the persisted transcript,
    ending in the newest user message) to Gemini, execute every real tool
    call it requests against `sim.api.analyst_tools.TOOL_REGISTRY`, feed
    each real result back, and repeat until Gemini returns final text with
    no further tool calls (or `max_rounds` is exhausted).

    `client` is injectable so tests can supply a fake `genai.Client` (or a
    stand-in with the same `.models.generate_content` surface) and verify
    the tool-execution/citation-building logic with zero real network
    calls -- matching CLAUDE.md's "fixtures, not live calls" discipline
    extended to this new external dependency.

    Raises `params_loader.LeagueNotIngestedError` /
    `analyst_tools.UnknownAnalystTeamError` (via `build_context`) for a
    bad league/team_id -- propagated unchanged, translated to HTTP 404 by
    the route handler exactly like every other per-team `sim.api` view.
    """
    ctx = build_context(conn, league_id, season_id, team_id)
    active_client = client if client is not None else _get_client()

    tool = types.Tool(function_declarations=TOOL_DECLARATIONS)
    config = types.GenerateContentConfig(
        system_instruction=_system_instruction(ctx),
        tools=[tool],
        # Manual loop only -- this module decides exactly when/how each
        # tool is called and logs every real result, rather than letting
        # the SDK call plain Python functions automatically and hide that
        # from the citation-building pass below.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        temperature=0.2,
    )

    contents: list[types.Content] = [
        types.Content(role=("user" if m.role == "user" else "model"), parts=[types.Part(text=m.text)])
        for m in history
    ]

    tool_calls: list[ToolCallLog] = []
    final_text = ""
    for _round in range(max_rounds):
        # google-genai's own `contents` parameter type is a `list[Union[...]]`
        # of several accepted element types (including TypedDict variants
        # like ContentDict); a `list[types.Content]` (an accurate, narrower
        # type for what this loop actually builds) is not itself a subtype of
        # that union under mypy's invariant-list rule, even though every
        # element genuinely satisfies it at runtime. A narrow `cast(Any, ...)`
        # at this ONE call boundary (not a blanket per-file ignore) is the
        # pragmatic fix -- the same class of "third-party stub is stricter
        # than the actual runtime contract" friction CLAUDE.md's mypy-strict
        # convention has hit before (see the apscheduler/scipy overrides in
        # this project's [[tool.mypy.overrides]]), except here the stub is
        # otherwise fine (google-genai ships py.typed) and only this one
        # invariant-list mismatch needs an escape hatch.
        response = active_client.models.generate_content(
            model=model, contents=cast(Any, contents), config=config
        )
        calls = response.function_calls
        if not calls:
            final_text = response.text or ""
            break

        if response.candidates and response.candidates[0].content is not None:
            contents.append(response.candidates[0].content)

        response_parts: list[types.Part] = []
        for call in calls:
            name = call.name or ""
            args: dict[str, Any] = dict(call.args or {})
            fn = TOOL_REGISTRY.get(name)
            if fn is None:
                result: dict[str, Any] = {"error": f"unknown tool '{name}' -- not one of the six real tools"}
            else:
                try:
                    result = fn(ctx, **args)
                except TypeError as exc:
                    result = {"error": f"invalid arguments for {name}: {exc}"}
            tool_calls.append(ToolCallLog(name=name, args=args, result=result))
            response_parts.append(types.Part.from_function_response(name=name, response=result))
        contents.append(types.Content(role="user", parts=response_parts))
    else:
        final_text = (
            "I made several tool calls but couldn't settle on a final answer within "
            "this turn's limit -- try asking again, a bit more specifically."
        )

    citations = _build_citations(tool_calls)
    spans = _match_citations_in_text(final_text, citations)
    return AnalystTurnResult(
        team_id=ctx.team_id,
        team_name=ctx.team_name,
        reply=final_text,
        citations=tuple(citations),
        spans=tuple(spans),
        tool_calls=tuple(tool_calls),
    )
