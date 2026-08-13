"""Tests for sim.api.analyst_tools -- the six real tool wrappers the AI
league analyst (PLAN.md Phase 13) exposes to Gemini.

Every tool wraps an already-tested `sim.api` view-module function (which
this file's other test modules already cover thoroughly for correctness),
so these tests focus on what is genuinely NEW here: the thin wrapping/
reshaping itself, team-name resolution, and -- most importantly -- that a
failure or an unresolvable name produces an honest `{"error": ...}` result
rather than raising or fabricating data (PLAN.md's explicit requirement).
No Gemini/network calls anywhere in this file.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import numpy as np
import psycopg
import pytest

from ingest.db import DEFAULT_TEST_DSN, ingest_league
from sim.api.analyst_tools import (
    AnalystContext,
    UnknownAnalystTeamError,
    build_context,
    tool_get_league_threats,
    tool_get_playoff_outlook,
    tool_get_roster_weaknesses,
    tool_get_team_odds,
    tool_get_trade_impact,
    tool_get_waiver_targets,
)
from sim.api.cache import write_cached_simulation
from sim.api.params_loader import load_league
from sim.engine import simulate_seasons

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)
FIXED_INGESTED_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture()
def league_ctx(pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]) -> AnalystContext:
    """Ingest the real league fixture and build an AnalystContext bound to
    its first real team -- the shared setup every tool test below needs."""
    ingest_league(pg_conn, raw_fixture, ingested_at=FIXED_INGESTED_AT)
    pg_conn.commit()
    league_id = raw_fixture["id"]
    season_id = raw_fixture["seasonId"]
    team_id = raw_fixture["teams"][0]["id"]
    return build_context(pg_conn, league_id, season_id, team_id)


def test_build_context_raises_for_unknown_team(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]
) -> None:
    ingest_league(pg_conn, raw_fixture, ingested_at=FIXED_INGESTED_AT)
    pg_conn.commit()
    with pytest.raises(UnknownAnalystTeamError):
        build_context(pg_conn, raw_fixture["id"], raw_fixture["seasonId"], 999_999)


# --------------------------------------------------------------------------
# get_team_odds
# --------------------------------------------------------------------------


def test_get_team_odds_honest_error_when_no_cache(league_ctx: AnalystContext) -> None:
    result = tool_get_team_odds(league_ctx)
    assert "error" in result
    assert "not" in result["error"].lower() or "no" in result["error"].lower()


def test_get_team_odds_returns_real_sorted_teams(league_ctx: AnalystContext) -> None:
    loaded = load_league(league_ctx.conn, league_ctx.league_id, league_ctx.season_id)
    sim_result = simulate_seasons(loaded.league, n_sims=500, rng=np.random.default_rng(7))
    write_cached_simulation(
        league_ctx.conn,
        league_ctx.league_id,
        league_ctx.season_id,
        sim_result,
        seed=7,
        computed_at=FIXED_INGESTED_AT,
    )
    league_ctx.conn.commit()

    result = tool_get_team_odds(league_ctx)
    assert "error" not in result
    assert result["your_team_id"] == league_ctx.team_id
    teams = result["teams"]
    assert len(teams) == len(league_ctx.team_names_by_id)
    # Sorted descending by title_probability, and title_rank matches that order.
    probs = [t["title_probability"] for t in teams]
    assert probs == sorted(probs, reverse=True)
    assert [t["title_rank"] for t in teams] == list(range(1, len(teams) + 1))
    # No finish_distribution array leaked through -- kept compact for the model.
    assert "finish_distribution" not in teams[0]


def test_get_team_odds_unknown_team_name_is_honest_error(league_ctx: AnalystContext) -> None:
    loaded = load_league(league_ctx.conn, league_ctx.league_id, league_ctx.season_id)
    sim_result = simulate_seasons(loaded.league, n_sims=200, rng=np.random.default_rng(1))
    write_cached_simulation(
        league_ctx.conn, league_ctx.league_id, league_ctx.season_id, sim_result,
        seed=1, computed_at=FIXED_INGESTED_AT,
    )
    league_ctx.conn.commit()

    result = tool_get_team_odds(league_ctx, team_name="Definitely Not A Real Team Name")
    assert "error" in result
    assert "Real team names" in result["error"]


# --------------------------------------------------------------------------
# get_roster_weaknesses
# --------------------------------------------------------------------------


def test_get_roster_weaknesses_returns_real_data(league_ctx: AnalystContext) -> None:
    result = tool_get_roster_weaknesses(league_ctx)
    assert "error" not in result
    assert result["team_id"] == league_ctx.team_id
    assert 0.0 <= result["risk_rating"] <= 1.0
    assert isinstance(result["positional_concentration"], list)
    assert len(result["starters"]) > 0
    for p in result["starters"]:
        assert p["mean_points_per_game"] > 0
        assert 0.0 <= p["availability"] <= 1.0


# --------------------------------------------------------------------------
# get_waiver_targets
# --------------------------------------------------------------------------


def test_get_waiver_targets_returns_real_groups(league_ctx: AnalystContext) -> None:
    result = tool_get_waiver_targets(league_ctx)
    assert "error" not in result
    assert len(result["groups"]) > 0
    for group in result["groups"]:
        for c in group["candidates"]:
            assert c["player_name"]
            assert c["reasoning"]


def test_get_waiver_targets_unknown_position_is_honest_error(league_ctx: AnalystContext) -> None:
    result = tool_get_waiver_targets(league_ctx, position="LONGSNAPPER")
    assert "error" in result
    assert "position group" in result["error"]


def test_get_waiver_targets_filters_to_real_position(league_ctx: AnalystContext) -> None:
    full = tool_get_waiver_targets(league_ctx)
    a_real_position = full["groups"][0]["position"]
    filtered = tool_get_waiver_targets(league_ctx, position=a_real_position.lower())
    assert "error" not in filtered
    assert len(filtered["groups"]) == 1
    assert filtered["groups"][0]["position"] == a_real_position


# --------------------------------------------------------------------------
# get_playoff_outlook
# --------------------------------------------------------------------------


def test_get_playoff_outlook_returns_real_data(league_ctx: AnalystContext) -> None:
    result = tool_get_playoff_outlook(league_ctx)
    assert "error" not in result
    assert result["team_id"] == league_ctx.team_id
    assert 0.0 <= result["title_probability"] <= 1.0
    assert result["recommendation"]
    assert len(result["slot_strengths"]) > 0


# --------------------------------------------------------------------------
# get_league_threats
# --------------------------------------------------------------------------


def test_get_league_threats_returns_real_data(league_ctx: AnalystContext) -> None:
    result = tool_get_league_threats(league_ctx)
    assert "error" not in result
    assert result["biggest_threat"]["team_name"]
    assert result["biggest_threat"]["reasoning"]
    assert result["real_advantage"]["reasoning"]
    assert isinstance(result["trade_cautions"], list)


def test_get_league_threats_for_named_rival(league_ctx: AnalystContext) -> None:
    other_name = next(
        name for tid, name in league_ctx.team_names_by_id.items() if tid != league_ctx.team_id
    )
    result = tool_get_league_threats(league_ctx, team_name=other_name)
    assert "error" not in result
    assert result["team_name"] == other_name


# --------------------------------------------------------------------------
# get_trade_impact
# --------------------------------------------------------------------------


def test_get_trade_impact_requires_named_players(league_ctx: AnalystContext) -> None:
    other_name = next(
        name for tid, name in league_ctx.team_names_by_id.items() if tid != league_ctx.team_id
    )
    result = tool_get_trade_impact(league_ctx, rival_team_name=other_name)
    assert "error" in result
    assert "needs specific real players" in result["error"]


def test_get_trade_impact_rejects_mismatched_counts(league_ctx: AnalystContext) -> None:
    other_name = next(
        name for tid, name in league_ctx.team_names_by_id.items() if tid != league_ctx.team_id
    )
    result = tool_get_trade_impact(
        league_ctx,
        rival_team_name=other_name,
        give_player_names=["A", "B"],
        receive_player_names=["C"],
    )
    assert "error" in result
    assert "equal-count" in result["error"]


def test_get_trade_impact_rejects_unresolvable_rival(league_ctx: AnalystContext) -> None:
    result = tool_get_trade_impact(
        league_ctx,
        rival_team_name="Not A Real Team",
        give_player_names=["Whoever"],
        receive_player_names=["Whoever Else"],
    )
    assert "error" in result


def test_get_trade_impact_rejects_non_roster_player(league_ctx: AnalystContext) -> None:
    other_name = next(
        name for tid, name in league_ctx.team_names_by_id.items() if tid != league_ctx.team_id
    )
    result = tool_get_trade_impact(
        league_ctx,
        rival_team_name=other_name,
        give_player_names=["Definitely Not A Real Player"],
        receive_player_names=["Also Not Real"],
    )
    assert "error" in result
    assert "not a real" in result["error"]


def test_get_trade_impact_real_trade_returns_before_after(league_ctx: AnalystContext) -> None:
    other_id, other_name = next(
        (tid, name) for tid, name in league_ctx.team_names_by_id.items() if tid != league_ctx.team_id
    )
    loaded = load_league(league_ctx.conn, league_ctx.league_id, league_ctx.season_id)
    my_idx = next(i for i, t in enumerate(loaded.league.teams) if t.team_id == league_ctx.team_id)
    rival_idx = next(i for i, t in enumerate(loaded.league.teams) if t.team_id == other_id)

    # Resolve real starter names via the raw payload's own player pool, the
    # same way the tool itself does, so this test trades two genuinely real
    # current starters rather than assuming any specific player exists.
    from ingest.parse import parse_player_pool, parse_scoring_table
    from sim.api.params_loader import load_raw_payload

    raw = load_raw_payload(league_ctx.conn, league_ctx.league_id, league_ctx.season_id)
    scoring_table = parse_scoring_table(raw)
    player_pool, _ = parse_player_pool(raw, scoring_table)
    name_by_id = {p.player_id: p.name for p in player_pool}

    my_player_name = name_by_id[loaded.league.teams[my_idx].starters[0].player_id]
    rival_player_name = name_by_id[loaded.league.teams[rival_idx].starters[0].player_id]

    result = tool_get_trade_impact(
        league_ctx,
        rival_team_name=other_name,
        give_player_names=[my_player_name],
        receive_player_names=[rival_player_name],
    )
    assert "error" not in result, result
    assert result["you_give"] == [my_player_name]
    assert result["you_receive"] == [rival_player_name]
    for side in ("your_team", "rival_team"):
        for key in (
            "title_probability_before",
            "title_probability_after",
            "playoff_probability_before",
            "playoff_probability_after",
        ):
            assert 0.0 <= result[side][key] <= 1.0
        # delta is real subtraction over the two above, not a separate guess
        assert result[side]["title_probability_delta"] == pytest.approx(
            result[side]["title_probability_after"] - result[side]["title_probability_before"]
        )


# --------------------------------------------------------------------------
# Every tool result must be genuinely JSON-serializable -- this is a real
# regression class, not a hypothetical one: a live run during this phase's
# own manual verification found get_playoff_outlook's projected_seed was a
# bare numpy.int64 (from scipy.optimize.linear_sum_assignment inside
# sim.api.playoff_planner_view), which sim/api/app.py's Pydantic response
# model silently coerces on the HTTP path but which crashed google-genai's
# own json.dumps() of the raw tool-result dict outright. Fixed with an
# explicit int(...) cast in tool_get_playoff_outlook; this test exists so a
# similar numpy-leak in any of the six tools fails a fast, offline test
# instead of only a live (paid) Gemini call.
# --------------------------------------------------------------------------


def test_every_tool_result_is_json_serializable(league_ctx: AnalystContext) -> None:
    loaded = load_league(league_ctx.conn, league_ctx.league_id, league_ctx.season_id)
    sim_result = simulate_seasons(loaded.league, n_sims=200, rng=np.random.default_rng(11))
    write_cached_simulation(
        league_ctx.conn, league_ctx.league_id, league_ctx.season_id, sim_result,
        seed=11, computed_at=FIXED_INGESTED_AT,
    )
    league_ctx.conn.commit()

    results = [
        tool_get_team_odds(league_ctx),
        tool_get_roster_weaknesses(league_ctx),
        tool_get_waiver_targets(league_ctx),
        tool_get_playoff_outlook(league_ctx),
        tool_get_league_threats(league_ctx),
        tool_get_trade_impact(league_ctx, rival_team_name="Not A Real Team"),  # honest error path
    ]
    for result in results:
        json.dumps(result)  # raises TypeError if anything non-JSON-safe leaked through

    # projected_seed specifically: must be a plain int (or None), not numpy.int64.
    outlook = tool_get_playoff_outlook(league_ctx)
    assert outlook["projected_seed"] is None or isinstance(outlook["projected_seed"], int)
