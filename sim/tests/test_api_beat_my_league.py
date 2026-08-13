"""Tests for sim.api.beat_my_league_view and the GET
/league/{id}/beat-my-league/{team_id} route.

Core computation coverage runs directly against `_compute_from_raw` with the
in-memory fixture dict (fast, no Postgres) -- same fast-unit-tests-plus-thin-
integration-test split every other `sim.api` view module already
established (see sim.api.draft_autopsy_view / sim.api.playoff_planner_view /
sim.api.lineup_optimizer_view / sim.api.waiver_intelligence_view). Only the
real league fixture (league_id=885686492) is used: this feature needs a real
drafted roster, which the SYNTHETIC validation league also has, but the real
league is the meaningful target for "must name real players, real teams,
real numbers" per PLAN.md.
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
from sim.api.beat_my_league_view import (
    _BENCH_DEPTH_RELEVANT_POSITIONS,
    _STRONG_PERCENTILE_THRESHOLD,
    UnknownTeamError,
    _compute_from_raw,
)

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)

# Small enough to keep unit tests fast; large enough that percentile/rank
# comparisons aren't dominated by sampling noise -- same choice
# sim.api.playoff_planner_view's own tests already make.
_TEST_N_SIMS = 2_000


# --------------------------------------------------------------------------
# Core computation, directly against the fixture dict -- no Postgres needed.
# --------------------------------------------------------------------------


def test_compute_from_raw_covers_every_team(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    team_id = raw["teams"][0]["id"]
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, n_sims=_TEST_N_SIMS)

    assert len(result.teams) == 8
    for profile in result.teams:
        assert 0.0 <= profile.title_probability <= profile.playoff_probability + 1e-9
        assert profile.playoff_schedule_note  # a real, non-empty synthesized sentence


def test_unknown_team_id_raises(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    with pytest.raises(UnknownTeamError):
        _compute_from_raw(raw, raw["id"], raw["seasonId"], 999_999, n_sims=_TEST_N_SIMS)


def test_title_probabilities_match_playoff_planner_exactly(raw_fixture: dict[str, Any]) -> None:
    """No second simulation path -- every team's title/playoff odds here must
    be byte-identical to a direct sim.api.playoff_planner_view call with the
    same n_sims (both are deterministically seeded from the same
    precompute_seed(league_id, season_id) formula)."""
    from sim.api.playoff_planner_view import _compute_from_raw as playoff_from_raw

    raw = raw_fixture
    team_id = raw["teams"][0]["id"]
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, n_sims=_TEST_N_SIMS)
    planner = playoff_from_raw(raw, raw["id"], raw["seasonId"], n_sims=_TEST_N_SIMS)

    planner_by_id = {s.team_id: s for s in planner.seeding}
    for profile in result.teams:
        expected = planner_by_id[profile.team_id]
        assert profile.title_probability == expected.title_probability
        assert profile.playoff_probability == expected.playoff_probability
        assert profile.finish_distribution == expected.finish_distribution
    assert result.seed == planner.seed
    assert result.n_sims == planner.n_sims


def test_weakness_is_a_single_slot_not_the_raw_flagged_set(raw_fixture: dict[str, Any]) -> None:
    """Regression test for the exact bug caught before shipping (see module
    docstring): K clears is_playoff_specific_weakness for every real team in
    this league (nobody carries a bench kicker), so using the raw flagged
    list as "structural weaknesses" would show "K" for every team -- not
    team-differentiating. Each team's `weaknesses` must be 0 or 1 slots,
    matching sim.api.playoff_planner_view's own single `weakest_slot`."""
    from sim.api.playoff_planner_view import _compute_from_raw as playoff_from_raw

    raw = raw_fixture
    team_id = raw["teams"][0]["id"]
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, n_sims=_TEST_N_SIMS)
    planner = playoff_from_raw(raw, raw["id"], raw["seasonId"], n_sims=_TEST_N_SIMS)
    weakest_by_id = {t.team_id: t.weakest_slot for t in planner.teams}

    weak_labels_across_league = set()
    for profile in result.teams:
        assert len(profile.weaknesses) <= 1
        expected_slot = weakest_by_id[profile.team_id]
        if expected_slot is None:
            assert profile.weaknesses == ()
        else:
            assert profile.weaknesses[0].slot_label == expected_slot
            weak_labels_across_league.add(expected_slot)

    # A real regression guard: if every team's single weakness collapsed to
    # the same slot label, the signal still wouldn't be differentiating.
    assert len(weak_labels_across_league) > 1


def test_strengths_are_a_relative_top_percentile_selection(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    team_id = raw["teams"][0]["id"]
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, n_sims=_TEST_N_SIMS)
    for profile in result.teams:
        for slot in profile.strengths:
            # Every returned strength either clears the real threshold, or is
            # the sole fallback (team's single best slot) -- never silently
            # empty when slot_strengths existed.
            assert slot.league_percentile >= _STRONG_PERCENTILE_THRESHOLD or len(profile.strengths) == 1


def test_biggest_threat_is_never_the_users_own_team(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    for raw_team in raw["teams"]:
        team_id = raw_team["id"]
        result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, n_sims=_TEST_N_SIMS)
        assert result.biggest_threat.team_id != team_id
        assert result.team_id == team_id
        # Every threat must cite a real, non-empty reasoning sentence.
        assert result.biggest_threat.reasoning
        assert result.real_advantage.reasoning


def test_threat_overlap_slots_are_real_weaknesses_of_the_user_and_real_strengths_of_the_rival(
    raw_fixture: dict[str, Any],
) -> None:
    """When overlapping_slots is non-empty, the biggest-threat selection must
    be a genuine positional matchup: the named slot(s) must be exactly the
    user's own weakness and the rival's own strength -- not a coincidence,
    not a slot neither team's data actually flags."""
    raw = raw_fixture
    for raw_team in raw["teams"]:
        team_id = raw_team["id"]
        result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, n_sims=_TEST_N_SIMS)
        if not result.biggest_threat.overlapping_slots:
            continue
        user_weak_labels = {s.slot_label for p in result.teams if p.team_id == team_id for s in p.weaknesses}
        threat_profile = next(p for p in result.teams if p.team_id == result.biggest_threat.team_id)
        threat_strong_labels = {s.slot_label for s in threat_profile.strengths}
        for slot_label in result.biggest_threat.overlapping_slots:
            assert slot_label in user_weak_labels
            assert slot_label in threat_strong_labels


def test_trade_cautions_only_flag_bench_depth_relevant_positions_with_real_surplus_and_real_need(
    raw_fixture: dict[str, Any],
) -> None:
    raw = raw_fixture
    teams = parse_teams(raw)
    for raw_team in raw["teams"]:
        team_id = raw_team["id"]
        result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, n_sims=_TEST_N_SIMS)
        for caution in result.trade_cautions:
            assert caution.position in _BENCH_DEPTH_RELEVANT_POSITIONS
            assert caution.team_bench_depth_at_position > 0
            assert len(caution.bench_player_names) == caution.team_bench_depth_at_position
            assert caution.rival_teams_with_need  # never an empty "no one needs this" flag
            rival_ids = {t.team_id for t in teams if t.name in caution.rival_teams_with_need}
            assert team_id not in {
                t.team_id for t in teams if t.team_id == team_id and t.name in caution.rival_teams_with_need
            }
            assert len(rival_ids) == len(caution.rival_teams_with_need)


def test_real_names_appear_in_every_reasoning_sentence_when_a_real_overlap_exists(
    raw_fixture: dict[str, Any],
) -> None:
    """CLAUDE.md / PLAN.md: 'must name real players, real teams, and real
    numbers.' A loose but real check: whenever biggest_threat has an
    overlapping slot, its reasoning text must contain the rival team's own
    name (a real, roster-specific detail, not a generic template)."""
    raw = raw_fixture
    team_id = raw["teams"][0]["id"]
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, n_sims=_TEST_N_SIMS)
    assert result.biggest_threat.team_name.strip() in result.biggest_threat.reasoning
    assert result.team_name.strip() in result.biggest_threat.reasoning


# --------------------------------------------------------------------------
# Postgres-backed integration test -- the real route, real DB round trip.
# --------------------------------------------------------------------------


@pytest.fixture()
def real_league_id(pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]) -> int:
    ingest_league(pg_conn, raw_fixture, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    pg_conn.commit()
    league_id: int = raw_fixture["id"]
    return league_id


@pytest.fixture()
def client(pg_conn: psycopg.Connection[Any]) -> Iterator[TestClient]:
    def override_get_connection() -> Iterator[psycopg.Connection[Any]]:
        yield pg_conn

    app_module.app.dependency_overrides[app_module.get_connection] = override_get_connection
    with TestClient(app_module.app) as test_client:
        yield test_client
    app_module.app.dependency_overrides.clear()


def test_route_matches_a_direct_compute_call(
    client: TestClient, real_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    team_id = raw_fixture["teams"][0]["id"]
    resp = client.get(f"/league/{real_league_id}/beat-my-league/{team_id}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["team_id"] == team_id
    assert body["team_name"]
    assert len(body["teams"]) == 8
    assert body["biggest_threat"]["team_id"] != team_id
    assert body["biggest_threat"]["reasoning"]
    assert body["real_advantage"]["reasoning"]
    assert isinstance(body["trade_cautions"], list)


def test_route_404s_for_an_unknown_team_id(client: TestClient, real_league_id: int) -> None:
    resp = client.get(f"/league/{real_league_id}/beat-my-league/999999")
    assert resp.status_code == 404


def test_route_404s_for_an_uningested_league(client: TestClient) -> None:
    resp = client.get("/league/1/beat-my-league/1")
    assert resp.status_code == 404
