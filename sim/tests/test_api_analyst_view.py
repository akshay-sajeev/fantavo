"""Tests for sim.api.analyst_view -- the Gemini tool-calling loop and the
deterministic citation-matching pass over its results.

Two kinds of coverage:
1. Pure, fast, zero-network tests of `_extract_citable_numbers` and
   `_match_citations_in_text` -- these never touch Gemini or Postgres.
2. `run_analyst_turn` exercised end-to-end against a FAKE `genai.Client`
   stand-in (injected via the `client` parameter) that returns real
   `google.genai.types.GenerateContentResponse` objects built by hand, so
   the tool-execution/citation-building logic is verified without a real
   network call -- matching CLAUDE.md's "fixtures, not live calls"
   discipline extended to this new external dependency (see
   sim.api.analyst_view's module docstring).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from google.genai import types

from ingest.db import DEFAULT_TEST_DSN, ingest_league
from sim.api.analyst_view import (
    AnalystMessage,
    Citation,
    _extract_citable_numbers,
    _match_citations_in_text,
    run_analyst_turn,
)

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)
FIXED_INGESTED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _has(
    out: list[tuple[str, str, float]], kind: str, subject: str, percent: float
) -> bool:
    """`(kind, subject, percent) in out`, with `percent` compared by
    floating-point tolerance instead of exact equality -- a plain helper
    function types cleanly under mypy --strict, unlike mixing
    `pytest.approx` values into a tuple membership check."""
    return any(
        k == kind and s == subject and abs(p - percent) < 1e-6 for k, s, p in out
    )


# --------------------------------------------------------------------------
# _extract_citable_numbers -- pure field extraction, no network.
# --------------------------------------------------------------------------


def test_extract_citable_numbers_returns_nothing_for_an_error_result() -> None:
    assert _extract_citable_numbers("get_team_odds", {"error": "nope"}) == []


def test_extract_citable_numbers_team_odds_is_on_percent_scale() -> None:
    result = {
        "teams": [
            {"team_name": "Alpha", "title_probability": 0.223, "playoff_probability": 0.6},
        ]
    }
    out = _extract_citable_numbers("get_team_odds", result)
    assert _has(out, "title_probability", "Alpha", 22.3)
    assert _has(out, "playoff_probability", "Alpha", 60.0)


def test_extract_citable_numbers_waiver_percent_owned_is_not_double_scaled() -> None:
    """percent_owned already arrives on a 0-100 ESPN scale (see
    sim.api.waiver_intelligence_view) -- must NOT be multiplied by 100 again."""
    result = {
        "groups": [
            {
                "candidates": [
                    {
                        "player_name": "Some Player",
                        "opportunity_score": 0.5,
                        "percent_owned": 42.0,
                    }
                ]
            }
        ]
    }
    out = _extract_citable_numbers("get_waiver_targets", result)
    assert _has(out, "opportunity_score", "Some Player", 50.0)
    assert _has(out, "percent_owned", "Some Player", 42.0)


def test_extract_citable_numbers_trade_impact_covers_both_sides() -> None:
    result = {
        "your_team_name": "Mine",
        "rival_team_name": "Theirs",
        "your_team": {
            "title_probability_before": 0.1,
            "title_probability_after": 0.15,
            "playoff_probability_before": 0.4,
            "playoff_probability_after": 0.45,
        },
        "rival_team": {
            "title_probability_before": 0.2,
            "title_probability_after": 0.18,
            "playoff_probability_before": 0.5,
            "playoff_probability_after": 0.48,
        },
    }
    out = _extract_citable_numbers("get_trade_impact", result)
    assert _has(out, "title_probability_before", "Mine", 10.0)
    assert _has(out, "title_probability_after", "Theirs", 18.0)
    assert len(out) == 8  # 4 fields x 2 sides


# --------------------------------------------------------------------------
# _match_citations_in_text -- pure, deterministic text matching, no network.
# --------------------------------------------------------------------------


def _citation(index: int, percent: float) -> Citation:
    return Citation(
        index=index, source_tool="get_team_odds", kind="title_probability",
        subject="Alpha", percent=percent, display=f"{percent:.1f}%",
    )


def test_match_citations_finds_an_exact_percentage() -> None:
    citations = [_citation(0, 22.3)]
    spans = _match_citations_in_text("You have a 22.3% title chance.", citations)
    assert len(spans) == 1
    assert spans[0].citation_index == 0
    assert "22.3%" == "You have a 22.3% title chance."[spans[0].start : spans[0].end]


def test_match_citations_tolerates_rounding_to_whole_percent() -> None:
    citations = [_citation(0, 22.6)]
    spans = _match_citations_in_text("Roughly 23% to win it all.", citations)
    assert len(spans) == 1
    assert spans[0].citation_index == 0


def test_match_citations_leaves_unrelated_percentage_unlinked() -> None:
    citations = [_citation(0, 22.3)]
    spans = _match_citations_in_text("A totally unrelated 91% shows up here.", citations)
    assert spans == []


def test_match_citations_picks_the_closest_of_several_candidates() -> None:
    citations = [_citation(0, 20.0), _citation(1, 20.3)]
    spans = _match_citations_in_text("About 20.3% this time.", citations)
    assert len(spans) == 1
    assert spans[0].citation_index == 1


def test_match_citations_handles_multiple_percentages_in_one_reply() -> None:
    citations = [_citation(0, 22.3), _citation(1, 61.0)]
    text = "Title odds are 22.3% and playoff odds are 61.0%."
    spans = _match_citations_in_text(text, citations)
    assert {s.citation_index for s in spans} == {0, 1}


# --------------------------------------------------------------------------
# run_analyst_turn -- end to end against a FAKE Gemini client, no network.
# --------------------------------------------------------------------------


class _FakeModels:
    """Stands in for `genai.Client().models` -- returns a pre-scripted
    sequence of real `types.GenerateContentResponse` objects, one per call,
    so the loop's tool-execution/citation-building logic can be verified
    without any real network call."""

    def __init__(self, responses: list[types.GenerateContentResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def generate_content(
        self, *, model: str, contents: Any, config: Any
    ) -> types.GenerateContentResponse:
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses: list[types.GenerateContentResponse]) -> None:
        self.models = _FakeModels(responses)


def _text_response(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]))]
    )


def _function_call_response(name: str, args: dict[str, Any]) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]
                )
            )
        ]
    )


@pytest.fixture()
def real_league(pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]) -> tuple[int, int, int]:
    ingest_league(pg_conn, raw_fixture, ingested_at=FIXED_INGESTED_AT)
    pg_conn.commit()
    return raw_fixture["id"], raw_fixture["seasonId"], raw_fixture["teams"][0]["id"]


def test_run_analyst_turn_executes_a_real_tool_and_returns_final_text(
    pg_conn: psycopg.Connection[Any], real_league: tuple[int, int, int]
) -> None:
    league_id, season_id, team_id = real_league

    fake = _FakeClient(
        [
            _function_call_response("get_roster_weaknesses", {}),
            _text_response("Your roster looks fine right now."),
        ]
    )

    result = run_analyst_turn(
        pg_conn, league_id, season_id, team_id,
        [AnalystMessage(role="user", text="What is my biggest weakness?")],
        client=fake,  # type: ignore[arg-type]
    )

    assert result.reply == "Your roster looks fine right now."
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "get_roster_weaknesses"
    # A REAL tool result -- risk_rating is a real field from
    # sim.api.roster_view, never fabricated by this fake test harness.
    assert "risk_rating" in result.tool_calls[0].result
    assert fake.models.calls[0]["config"].system_instruction  # real system prompt was built


def test_run_analyst_turn_reports_an_honest_tool_error_when_cache_missing(
    pg_conn: psycopg.Connection[Any], real_league: tuple[int, int, int]
) -> None:
    league_id, season_id, team_id = real_league

    fake = _FakeClient(
        [
            _function_call_response("get_team_odds", {}),
            _text_response("I couldn't get real odds for you -- the simulation isn't cached yet."),
        ]
    )

    result = run_analyst_turn(
        pg_conn, league_id, season_id, team_id,
        [AnalystMessage(role="user", text="Why am I projected to lose?")],
        client=fake,  # type: ignore[arg-type]
    )

    assert "error" in result.tool_calls[0].result
    assert result.citations == ()  # no citable numbers from a failed tool call


def test_run_analyst_turn_produces_matched_citation_spans(
    pg_conn: psycopg.Connection[Any], real_league: tuple[int, int, int]
) -> None:
    league_id, season_id, team_id = real_league

    import numpy as np

    from sim.api.cache import write_cached_simulation
    from sim.api.params_loader import load_league
    from sim.engine import simulate_seasons

    loaded = load_league(pg_conn, league_id, season_id)
    sim_result = simulate_seasons(loaded.league, n_sims=500, rng=np.random.default_rng(3))
    write_cached_simulation(pg_conn, league_id, season_id, sim_result, seed=3, computed_at=FIXED_INGESTED_AT)
    pg_conn.commit()

    my_idx = next(i for i, t in enumerate(sim_result.team_ids) if t == team_id)
    real_title_pct = round(float(sim_result.won_title[my_idx]) * 100, 1)

    fake = _FakeClient(
        [
            _function_call_response("get_team_odds", {}),
            _text_response(f"You have a {real_title_pct:.1f}% title probability."),
        ]
    )

    result = run_analyst_turn(
        pg_conn, league_id, season_id, team_id,
        [AnalystMessage(role="user", text="What are my odds?")],
        client=fake,  # type: ignore[arg-type]
    )

    assert len(result.citations) > 0
    assert len(result.spans) == 1
    cited = result.citations[result.spans[0].citation_index]
    assert cited.percent == pytest.approx(real_title_pct, abs=0.5)


def test_run_analyst_turn_gives_up_gracefully_after_max_rounds(
    pg_conn: psycopg.Connection[Any], real_league: tuple[int, int, int]
) -> None:
    league_id, season_id, team_id = real_league
    # Every round requests a tool call, never a final answer -- the loop
    # must not hang or crash, just stop honestly at max_rounds.
    fake = _FakeClient([_function_call_response("get_roster_weaknesses", {}) for _ in range(3)])

    result = run_analyst_turn(
        pg_conn, league_id, season_id, team_id,
        [AnalystMessage(role="user", text="Keep asking forever?")],
        client=fake,  # type: ignore[arg-type]
        max_rounds=3,
    )

    assert "try asking again" in result.reply
    assert len(result.tool_calls) == 3


def test_run_analyst_turn_unknown_tool_name_is_an_honest_error(
    pg_conn: psycopg.Connection[Any], real_league: tuple[int, int, int]
) -> None:
    league_id, season_id, team_id = real_league
    fake = _FakeClient(
        [
            _function_call_response("not_a_real_tool", {}),
            _text_response("Sorry, I couldn't find that."),
        ]
    )
    result = run_analyst_turn(
        pg_conn, league_id, season_id, team_id,
        [AnalystMessage(role="user", text="Do something weird")],
        client=fake,  # type: ignore[arg-type]
    )
    assert "error" in result.tool_calls[0].result
