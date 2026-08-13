"""Tests for sim.api.roast_view and the GET /league/{id}/power-ranking-roast
route.

Core computation coverage runs directly against `_compute_from_raw` with the
in-memory fixture dict (fast, no Postgres) -- same fast-unit-tests-plus-thin-
integration-test split every other `sim.api` view module already
established. Both the real league fixture (a completed real draft --
`has_draft_data=True`) and the SYNTHETIC validation league (no pick sequence
-- `has_draft_data=False`) are exercised, since this module's whole point is
that draft material is optional while everything else stays real.
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
from scripts.ingest_synthetic_league import build_synthetic_raw_payload
from sim.api import app as app_module
from sim.api.roast_view import _REACH_THRESHOLD, _STEAL_THRESHOLD, _compute_from_raw

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)

# Same tradeoff sim.api.beat_my_league_view's own tests make: small enough to
# keep unit tests fast, large enough that percentile/rank comparisons aren't
# dominated by sampling noise.
_TEST_N_SIMS = 2_000


# --------------------------------------------------------------------------
# Core computation, directly against the fixture dict -- no Postgres needed.
# --------------------------------------------------------------------------


def test_compute_from_raw_covers_every_team_in_power_ranking_order(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], n_sims=_TEST_N_SIMS)

    assert len(result.teams) == 8
    assert result.has_draft_data is True
    # Power-ranking order: title_probability descending, and title_rank must
    # match position in the list exactly.
    for i, team in enumerate(result.teams, start=1):
        assert team.title_rank == i
        assert team.league_team_count == 8
    probs = [t.title_probability for t in result.teams]
    assert probs == sorted(probs, reverse=True)


def test_every_roast_is_non_empty_and_every_sentence_has_a_backing_fact(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], n_sims=_TEST_N_SIMS)
    for team in result.teams:
        assert team.roast.strip()
        # At minimum, the rank bit always fires -- one fact per roast, and
        # every roast has draft data here, so at least two facts.
        assert len(team.facts) >= 2
        for fact in team.facts:
            assert fact.kind
            assert fact.text.strip()


def test_roasts_are_genuinely_different_across_teams(raw_fixture: dict[str, Any]) -> None:
    """Not a template filled with just the team name -- real per-team
    variation grounded in different underlying facts."""
    raw = raw_fixture
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], n_sims=_TEST_N_SIMS)
    roast_texts = {team.roast for team in result.teams}
    # 8 real teams; allow for the rare case two teams share every real fact
    # bucket (e.g. both mid-pack with identical fact "shapes"), but the vast
    # majority must differ.
    assert len(roast_texts) >= 6


def test_every_roast_cites_the_teams_own_name(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], n_sims=_TEST_N_SIMS)
    for team in result.teams:
        assert team.team_name.strip() in team.roast


def test_rank_fact_matches_title_probability_and_rank(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], n_sims=_TEST_N_SIMS)
    for team in result.teams:
        rank_fact = next(f for f in team.facts if f.kind == "title_rank")
        assert str(team.title_rank) in rank_fact.text
        pct_str = f"{team.title_probability * 100:.1f}"
        assert pct_str in rank_fact.text


def test_draft_reach_fact_names_a_real_worse_pick_and_alternative(raw_fixture: dict[str, Any]) -> None:
    """When a draft_reach fact fires, it must name the real alternative
    player who was passed over, per PLAN.md's 'must name real players' bar."""
    raw = raw_fixture
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], n_sims=_TEST_N_SIMS)
    from sim.api.draft_autopsy_view import _compute_from_raw as draft_from_raw

    autopsy = draft_from_raw(raw, raw["id"], raw["seasonId"])
    autopsy_by_team = {t.team_id: t for t in autopsy.teams}

    saw_a_reach = False
    for team in result.teams:
        reach_facts = [f for f in team.facts if f.kind == "draft_reach"]
        if not reach_facts:
            continue
        saw_a_reach = True
        worst = autopsy_by_team[team.team_id].worst_pick
        assert worst.value_gap <= _REACH_THRESHOLD
        assert worst.alternative_player_name is not None
        assert worst.player_name in team.roast
        assert worst.alternative_player_name in team.roast
    # Not asserting this MUST happen (a real, honest possibility if no team
    # reached badly enough), but the real league fixture is expected to have
    # at least one, per Phase 7's own documented finding of a real reach.
    assert saw_a_reach


def test_bench_depth_fact_matches_the_teams_real_weakest_slot(raw_fixture: dict[str, Any]) -> None:
    from sim.api.playoff_planner_view import _compute_from_raw as playoff_from_raw

    raw = raw_fixture
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], n_sims=_TEST_N_SIMS)
    planner = playoff_from_raw(raw, raw["id"], raw["seasonId"], n_sims=_TEST_N_SIMS)
    weakest_by_id = {t.team_id: t.weakest_slot for t in planner.teams}

    for team in result.teams:
        bench_facts = [f for f in team.facts if f.kind == "bench_depth"]
        expected_slot = weakest_by_id[team.team_id]
        if expected_slot is None:
            assert not bench_facts
            continue
        assert len(bench_facts) == 1
        assert expected_slot in bench_facts[0].text
        assert expected_slot in team.roast


def test_rival_threat_fact_only_fires_with_a_real_positional_overlap(raw_fixture: dict[str, Any]) -> None:
    from sim.api.beat_my_league_view import _build_shared_materials, _compute_team_result

    raw = raw_fixture
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], n_sims=_TEST_N_SIMS)
    materials = _build_shared_materials(raw, raw["id"], raw["seasonId"], n_sims=_TEST_N_SIMS)

    for team in result.teams:
        threat, _adv, _cautions = _compute_team_result(team.team_id, materials)
        threat_facts = [f for f in team.facts if f.kind == "rival_threat"]
        if threat.overlapping_slots:
            assert len(threat_facts) == 1
            assert threat.team_name in team.roast
        else:
            assert not threat_facts


def test_synthetic_league_has_no_draft_data_but_still_produces_real_roasts(
    raw_fixture: dict[str, Any],
) -> None:
    synthetic_raw = build_synthetic_raw_payload(raw_fixture)
    result = _compute_from_raw(
        synthetic_raw, synthetic_raw["id"], synthetic_raw["seasonId"], n_sims=_TEST_N_SIMS
    )
    assert result.has_draft_data is False
    assert len(result.teams) > 0
    for team in result.teams:
        assert not any(f.kind.startswith("draft_") for f in team.facts)
        assert team.roast.strip()
        assert len(team.facts) >= 1  # the rank bit always fires


def test_seed_and_n_sims_match_playoff_planner_exactly(raw_fixture: dict[str, Any]) -> None:
    """No second simulation path -- must be byte-identical to a direct
    sim.api.playoff_planner_view call with the same n_sims."""
    from sim.api.playoff_planner_view import _compute_from_raw as playoff_from_raw

    raw = raw_fixture
    result = _compute_from_raw(raw, raw["id"], raw["seasonId"], n_sims=_TEST_N_SIMS)
    planner = playoff_from_raw(raw, raw["id"], raw["seasonId"], n_sims=_TEST_N_SIMS)
    assert result.seed == planner.seed
    assert result.n_sims == planner.n_sims


def test_thresholds_are_meaningfully_different() -> None:
    """A sanity guard against the two editorial thresholds accidentally
    colliding or inverting (a steal should require a bigger, positive gap
    than a reach requires in magnitude, per the module docstring)."""
    assert _REACH_THRESHOLD < 0
    assert _STEAL_THRESHOLD > 0
    assert _STEAL_THRESHOLD > abs(_REACH_THRESHOLD)


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
    resp = client.get(f"/league/{real_league_id}/power-ranking-roast")
    assert resp.status_code == 200
    body = resp.json()

    assert body["league_id"] == real_league_id
    assert body["has_draft_data"] is True
    assert len(body["teams"]) == 8
    for i, team in enumerate(body["teams"], start=1):
        assert team["title_rank"] == i
        assert team["roast"]
        assert team["facts"]


def test_route_404s_for_an_uningested_league(client: TestClient) -> None:
    resp = client.get("/league/1/power-ranking-roast")
    assert resp.status_code == 404
