"""Tests for sim.api.waiver_intelligence_view and the GET
/league/{id}/waiver-intelligence/{team_id} route.

Most coverage runs directly against `_compute_from_raw` with the in-memory
fixture dict (fast, no Postgres) -- same fast-unit-tests-plus-thin-
integration-test split every other `sim.api` view module already
established (see sim.api.draft_autopsy_view / sim.api.playoff_planner_view /
sim.api.lineup_optimizer_view). Only the real league fixture
(league_id=885686492) is used: unlike Draft Autopsy this feature needs no
real draft pick sequence, only `_freeAgents` and real rosters, so the
SYNTHETIC validation league would also work, but the real league is the
more meaningful target since its `_freeAgents` block reflects a genuine
post-draft state.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import DEFAULT_TEST_DSN, ingest_league
from ingest.parse import parse_teams
from sim.api import app as app_module
from sim.api.waiver_intelligence_view import (
    _BENCH_DEPTH_RELEVANT_POSITIONS,
    _HARD_OUT_STATUSES,
    _POSITION_DISPLAY_ORDER,
    UnknownTeamError,
    _compute_from_raw,
    compute_waiver_intelligence,
)

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)


# --------------------------------------------------------------------------
# Core computation, directly against the fixture dict -- no Postgres needed.
# --------------------------------------------------------------------------


def test_compute_from_raw_returns_groups_in_canonical_order(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    team_id = raw["teams"][0]["id"]
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, limit_per_position=5)

    positions = [g.position for g in result.groups]
    # All six ESPN-standard positions have real free agents in this fixture
    # (verified directly), so every group should appear.
    assert set(positions) == set(_POSITION_DISPLAY_ORDER)
    # Groups that need attention (real positional need) must sort before
    # groups that don't; K/D-ST (bench_depth_relevant=False) always last.
    relevance_flags = [g.bench_depth_relevant for g in result.groups]
    # once a False (K/D-ST) group appears, no True group should follow it
    first_false = next((i for i, r in enumerate(relevance_flags) if not r), len(relevance_flags))
    assert all(relevance_flags[:first_false])


def test_only_qb_rb_wr_te_ever_show_a_positional_need(raw_fixture: dict[str, Any]) -> None:
    """K and D/ST bench depth is 0, or nearly 0, for literally every real
    team in this league (see module docstring's "caught before shipping"
    note) -- team_has_positional_need must never fire for either."""
    raw = raw_fixture
    teams = parse_teams(raw)
    for team in teams:
        result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team.team_id, limit_per_position=3)
        for group in result.groups:
            if group.position not in _BENCH_DEPTH_RELEVANT_POSITIONS:
                assert group.bench_depth_relevant is False
                assert group.team_has_positional_need is False
                assert group.rival_teams_with_need == ()
            else:
                assert group.bench_depth_relevant is True


def test_positional_need_genuinely_differs_across_teams(raw_fixture: dict[str, Any]) -> None:
    """The whole point of League fit (Signal 3) is that it's team-specific
    -- verify at least two teams disagree on which positions they need."""
    raw = raw_fixture
    teams = parse_teams(raw)
    needs_by_team: dict[int, frozenset[str]] = {}
    for team in teams:
        result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team.team_id, limit_per_position=3)
        needs_by_team[team.team_id] = frozenset(
            g.position for g in result.groups if g.team_has_positional_need
        )
    assert len(set(needs_by_team.values())) > 1, "every team showed the identical need set"


def test_candidates_within_a_group_are_sorted_by_expected_playable_points(
    raw_fixture: dict[str, Any],
) -> None:
    raw = raw_fixture
    team_id = raw["teams"][0]["id"]
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, limit_per_position=50)
    for group in result.groups:
        values = [c.expected_playable_points for c in group.candidates]
        assert values == sorted(values, reverse=True)


def test_limit_per_position_caps_each_group(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    team_id = raw["teams"][0]["id"]
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, limit_per_position=3)
    for group in result.groups:
        assert len(group.candidates) <= 3


def test_opportunity_score_is_zero_for_out_or_injury_reserve_players(
    raw_fixture: dict[str, Any],
) -> None:
    raw = raw_fixture
    team_id = raw["teams"][0]["id"]
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, limit_per_position=300)
    checked_any = False
    for group in result.groups:
        for c in group.candidates:
            if c.injury_status in _HARD_OUT_STATUSES:
                assert c.opportunity_score == 0.0
                assert c.expected_playable_points == 0.0
                checked_any = True
    assert checked_any, "expected at least one OUT/INJURY_RESERVE free agent in this fixture"


def test_opportunity_score_is_bounded_and_matches_start_rate_ratio_when_not_hard_out(
    raw_fixture: dict[str, Any],
) -> None:
    raw = raw_fixture
    team_id = raw["teams"][0]["id"]
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, limit_per_position=300)
    for group in result.groups:
        for c in group.candidates:
            assert 0.0 <= c.opportunity_score <= 1.0
            assert 0.0 <= c.start_rate_ratio <= 1.0
            if c.injury_status not in _HARD_OUT_STATUSES:
                assert c.opportunity_score == pytest.approx(c.start_rate_ratio)


def test_expected_playable_points_is_mean_times_opportunity(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    team_id = raw["teams"][0]["id"]
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, limit_per_position=300)
    for group in result.groups:
        for c in group.candidates:
            assert c.expected_playable_points == pytest.approx(
                c.mean_points_per_game * c.opportunity_score
            )


def test_reasoning_text_is_present_and_team_specific(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    teams = parse_teams(raw)
    team_a, team_b = teams[0], teams[1]
    result_a = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_a.team_id, limit_per_position=3)
    result_b = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_b.team_id, limit_per_position=3)

    for group in result_a.groups:
        assert group.group_reasoning
        assert team_a.name.strip() in group.group_reasoning
        for c in group.candidates:
            assert c.reasoning

    # Group reasoning must differ across teams for at least one shared
    # position (it's a team-specific narrative, not a template).
    groups_a = {g.position: g.group_reasoning for g in result_a.groups}
    groups_b = {g.position: g.group_reasoning for g in result_b.groups}
    shared_positions = set(groups_a) & set(groups_b)
    assert any(groups_a[p] != groups_b[p] for p in shared_positions)


def test_unknown_team_id_raises(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    with pytest.raises(UnknownTeamError):
        _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id=-999999, limit_per_position=5)


def test_every_real_team_produces_a_result(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    teams = parse_teams(raw)
    for team in teams:
        result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team.team_id, limit_per_position=5)
        assert result.team_id == team.team_id
        assert result.ownership_data_note
        assert len(result.groups) > 0


def test_result_is_deterministic(raw_fixture: dict[str, Any]) -> None:
    """No RNG anywhere in this module -- two calls with identical inputs
    must produce byte-identical results, not just statistically close ones."""
    raw = raw_fixture
    team_id = raw["teams"][0]["id"]
    first = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, limit_per_position=5)
    second = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, limit_per_position=5)
    assert first == second


# --------------------------------------------------------------------------
# Postgres-backed: route wiring and error paths.
# --------------------------------------------------------------------------


@pytest.fixture()
def client(
    pg_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    monkeypatch.setattr(app_module, "start_scheduler", lambda dsn=None: None)
    with TestClient(app_module.app) as test_client:
        yield test_client


def test_get_waiver_intelligence_returns_404_for_an_uningested_league(client: TestClient) -> None:
    response = client.get("/league/424242/waiver-intelligence/1")
    assert response.status_code == 404


def test_get_waiver_intelligence_returns_404_for_an_unknown_team(
    client: TestClient, pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]
) -> None:
    ingest_league(pg_conn, raw_fixture, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    pg_conn.commit()

    response = client.get(
        f"/league/{raw_fixture['id']}/waiver-intelligence/-999999",
        params={"season_id": raw_fixture["seasonId"]},
    )
    assert response.status_code == 404


def test_get_waiver_intelligence_route_matches_a_direct_compute_call(
    client: TestClient, pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]
) -> None:
    league_id = raw_fixture["id"]
    season_id = raw_fixture["seasonId"]
    team_id = raw_fixture["teams"][0]["id"]
    ingest_league(pg_conn, raw_fixture, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    pg_conn.commit()

    direct = compute_waiver_intelligence(pg_conn, league_id, season_id, team_id, limit_per_position=4)

    response = client.get(
        f"/league/{league_id}/waiver-intelligence/{team_id}",
        params={"season_id": season_id, "limit_per_position": 4},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["league_id"] == league_id
    assert body["team_id"] == team_id
    assert body["team_name"] == direct.team_name
    assert len(body["groups"]) == len(direct.groups)
    for out_group, direct_group in zip(body["groups"], direct.groups, strict=True):
        assert out_group["position"] == direct_group.position
        assert len(out_group["candidates"]) == len(direct_group.candidates)
        assert out_group["team_has_positional_need"] == direct_group.team_has_positional_need
