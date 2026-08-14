"""Tests for sim.api.playoff_planner_view and the GET
/league/{id}/playoff-planner route.

Grading-logic coverage runs directly against `_compute_from_raw` with the
in-memory fixture dict (fast, no Postgres) -- same split
sim.api.draft_autopsy_view already established. Both the real league
fixture and the SYNTHETIC validation league are exercised: unlike draft
autopsy, the Playoff Planner does not need a real pick sequence, only a
drafted roster and the league's own schedule settings, both of which the
SYNTHETIC league has (see docs/decisions.md Phase 8).
"""

from __future__ import annotations

import copy
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import DEFAULT_TEST_DSN, ingest_league
from sim.api import app as app_module
from sim.api.playoff_planner_view import (
    DEFAULT_N_SIMS,
    _compute_from_raw,
    compute_playoff_planner,
)
from sim.engine import bracket_pairings

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)

# Small enough to keep unit tests fast; large enough that percentile/rank
# comparisons aren't dominated by sampling noise.
_TEST_N_SIMS = 2_000


# --------------------------------------------------------------------------
# bracket_pairings -- the engine-level pairing rule this module reuses.
# --------------------------------------------------------------------------


def test_bracket_pairings_four_seeds_is_1v4_2v3() -> None:
    assert bracket_pairings(4) == ((0, 3), (1, 2))


def test_bracket_pairings_eight_seeds() -> None:
    assert bracket_pairings(8) == ((0, 7), (1, 6), (2, 5), (3, 4))


# --------------------------------------------------------------------------
# Grading logic, directly against the fixture dict -- no Postgres needed.
# --------------------------------------------------------------------------


def test_compute_from_raw_matches_this_leagues_known_settings(raw_fixture: dict[str, Any]) -> None:
    """DATA FACTS confirmed against the real fixture: 14 regular weeks, 4
    playoff teams, 2 playoff rounds -- derived from the league's own
    settings, not hardcoded here."""
    result = _compute_from_raw(
        raw_fixture, raw_fixture["id"], raw_fixture["seasonId"], n_sims=_TEST_N_SIMS
    )
    assert result.n_regular_weeks == 14
    assert result.n_playoff_teams == 4
    assert result.n_playoff_rounds == 2
    assert len(result.teams) == 8
    assert len(result.seeding) == 8


def test_seed_probabilities_sum_to_playoff_probability(raw_fixture: dict[str, Any]) -> None:
    result = _compute_from_raw(
        raw_fixture, raw_fixture["id"], raw_fixture["seasonId"], n_sims=_TEST_N_SIMS
    )
    for team in result.seeding:
        assert len(team.seed_probabilities) == result.n_playoff_teams
        assert sum(team.seed_probabilities) == pytest.approx(team.playoff_probability, abs=1e-9)
        assert 0.0 <= team.title_probability <= team.reached_final_probability <= team.playoff_probability + 1e-9


def test_seeding_matches_a_direct_simulate_seasons_call_with_the_same_seed(
    raw_fixture: dict[str, Any],
) -> None:
    """The seeding numbers this module reports must be exactly what a direct
    `simulate_seasons()` call with the module's own documented seed
    (`sim.api.seeds.precompute_seed`) produces -- proof this is a
    post-processing read of SimulationResult, not a second computation."""
    from ingest.parse import (
        build_team_params,
        parse_player_pool,
        parse_schedule,
        parse_scoring_table,
        parse_teams,
    )
    from sim.api.seeds import precompute_seed
    from sim.engine import LeagueParams, simulate_seasons
    from sim.params.derive import derive_player_params_pool
    from sim.params.variance import fit_position_cv

    raw = raw_fixture
    league_id, season_id = raw["id"], raw["seasonId"]

    scoring_table = parse_scoring_table(raw)
    player_pool, _ = parse_player_pool(raw, scoring_table)
    position_cv = fit_position_cv(raw)
    players_by_id = derive_player_params_pool(player_pool, position_cv)
    teams = parse_teams(raw)
    n_regular_weeks = raw["settings"]["scheduleSettings"]["matchupPeriodCount"]
    n_playoff_teams = raw["settings"]["scheduleSettings"]["playoffTeamCount"]
    schedule = parse_schedule(raw, teams, n_regular_weeks)
    team_params = tuple(build_team_params(raw, t.team_id, players_by_id) for t in teams)
    league = LeagueParams(teams=team_params, schedule=schedule.pairings, n_playoff_teams=n_playoff_teams)

    seed = precompute_seed(league_id, season_id)
    direct = simulate_seasons(league, n_sims=_TEST_N_SIMS, rng=np.random.default_rng(seed))

    result = _compute_from_raw(raw, league_id, season_id, n_sims=_TEST_N_SIMS)

    direct_by_id = dict(zip(direct.team_ids, direct.won_title, strict=True))
    for team in result.seeding:
        assert team.title_probability == pytest.approx(direct_by_id[team.team_id])
    assert result.seed == seed


def test_projected_bracket_is_1v4_2v3_and_names_real_teams(raw_fixture: dict[str, Any]) -> None:
    result = _compute_from_raw(
        raw_fixture, raw_fixture["id"], raw_fixture["seasonId"], n_sims=_TEST_N_SIMS
    )
    assert len(result.bracket) == 2  # 4 playoff teams -> 2 round-1 matchups
    seeds_seen = set()
    for matchup in result.bracket:
        seeds_seen.add(matchup.high_seed)
        seeds_seen.add(matchup.low_seed)
        # Every seed slot got a real, named team assigned (n_playoff_teams=4
        # <= n_teams=8, so the maximum-weight assignment always fills every
        # slot for this league).
        assert matchup.high_seed_team_id is not None
        assert matchup.high_seed_team_name is not None
        assert matchup.low_seed_team_id is not None
        assert matchup.low_seed_team_name is not None
    assert seeds_seen == {1, 2, 3, 4}
    # 1v4, 2v3 -- the standard bracket pairing, not reseeded ad hoc.
    pairs = {(m.high_seed, m.low_seed) for m in result.bracket}
    assert pairs == {(1, 4), (2, 3)}


def test_projected_seed_is_a_permutation_of_teams_assigned_to_the_bracket(
    raw_fixture: dict[str, Any],
) -> None:
    result = _compute_from_raw(
        raw_fixture, raw_fixture["id"], raw_fixture["seasonId"], n_sims=_TEST_N_SIMS
    )
    assigned_seeds = [t.projected_seed for t in result.seeding if t.projected_seed is not None]
    assert len(assigned_seeds) == result.n_playoff_teams
    assert sorted(assigned_seeds) == list(range(1, result.n_playoff_teams + 1))
    # Every team that appears in the bracket's named matchups is exactly the
    # set of teams with a non-null projected_seed.
    bracket_team_ids = {
        tid
        for m in result.bracket
        for tid in (m.high_seed_team_id, m.low_seed_team_id)
        if tid is not None
    }
    projected_team_ids = {t.team_id for t in result.seeding if t.projected_seed is not None}
    assert bracket_team_ids == projected_team_ids


def test_slot_strengths_cover_this_leagues_real_starting_slots(raw_fixture: dict[str, Any]) -> None:
    result = _compute_from_raw(
        raw_fixture, raw_fixture["id"], raw_fixture["seasonId"], n_sims=_TEST_N_SIMS
    )
    expected_slots = {"QB", "RB", "WR", "TE", "FLEX", "D/ST", "K"}
    for team in result.teams:
        seen = {s.slot_label for s in team.slot_strengths}
        assert seen == expected_slots
        assert team.recommendation  # never empty for a real, fully-rostered team


def test_floor_ratio_is_never_greater_than_one(raw_fixture: dict[str, Any]) -> None:
    """A 10th-percentile outcome can never exceed the mean for a
    right-skewed, non-negative distribution summed over positive weights."""
    result = _compute_from_raw(
        raw_fixture, raw_fixture["id"], raw_fixture["seasonId"], n_sims=_TEST_N_SIMS
    )
    for team in result.teams:
        for slot in team.slot_strengths:
            assert 0.0 <= slot.regular_floor_ratio <= 1.0
            assert 0.0 <= slot.playoff_floor_ratio <= 1.0


def test_playoff_specific_weakness_requires_both_a_real_delta_and_no_bench_depth(
    raw_fixture: dict[str, Any],
) -> None:
    result = _compute_from_raw(
        raw_fixture, raw_fixture["id"], raw_fixture["seasonId"], n_sims=_TEST_N_SIMS
    )
    for team in result.teams:
        for slot in team.slot_strengths:
            if slot.is_playoff_specific_weakness:
                assert not slot.has_bench_depth
                assert slot.bench_depth_relevant
                assert slot.floor_ratio_delta > 0


def test_playoff_specific_weakness_never_flags_kicker_or_d_st(
    raw_fixture: dict[str, Any],
) -> None:
    """K and D/ST are single-slot positions a real roster normally carries
    exactly one of -- zero bench depth there is expected, not a real
    per-team weakness (see `_BENCH_DEPTH_RELEVANT_POSITIONS`)."""
    result = _compute_from_raw(
        raw_fixture, raw_fixture["id"], raw_fixture["seasonId"], n_sims=_TEST_N_SIMS
    )
    for team in result.teams:
        for slot in team.slot_strengths:
            if slot.slot_label in ("K", "D/ST"):
                assert slot.bench_depth_relevant is False
                assert slot.is_playoff_specific_weakness is False


def test_league_rank_is_a_valid_permutation_per_slot(raw_fixture: dict[str, Any]) -> None:
    result = _compute_from_raw(
        raw_fixture, raw_fixture["id"], raw_fixture["seasonId"], n_sims=_TEST_N_SIMS
    )
    ranks_by_slot: dict[str, list[int]] = {}
    for team in result.teams:
        for slot in team.slot_strengths:
            ranks_by_slot.setdefault(slot.slot_label, []).append(slot.league_rank)
            assert slot.league_team_count == len(result.teams)
    for slot_label, ranks in ranks_by_slot.items():
        assert sorted(ranks) == list(range(1, len(result.teams) + 1)), slot_label


def test_weakest_slot_is_always_one_of_this_teams_own_slots(raw_fixture: dict[str, Any]) -> None:
    result = _compute_from_raw(
        raw_fixture, raw_fixture["id"], raw_fixture["seasonId"], n_sims=_TEST_N_SIMS
    )
    for team in result.teams:
        labels = {s.slot_label for s in team.slot_strengths}
        assert team.weakest_slot in labels


def test_recommendation_names_the_weakest_slot(raw_fixture: dict[str, Any]) -> None:
    result = _compute_from_raw(
        raw_fixture, raw_fixture["id"], raw_fixture["seasonId"], n_sims=_TEST_N_SIMS
    )
    for team in result.teams:
        if team.weakest_slot:
            assert team.weakest_slot in team.recommendation


def test_synthetic_league_also_produces_a_full_playoff_plan(
    raw_fixture: dict[str, Any],
) -> None:
    """Unlike draft autopsy, the SYNTHETIC validation league IS usable here:
    its settings (schedule length, playoff team count) are copied verbatim
    from the real league by scripts/ingest_synthetic_league.py, and it has a
    real, fully-drafted roster (just no real pick sequence) -- see
    docs/decisions.md Phase 8."""
    from scripts.ingest_synthetic_league import build_synthetic_raw_payload

    synthetic = build_synthetic_raw_payload(raw_fixture)
    result = _compute_from_raw(
        synthetic, synthetic["id"], synthetic["seasonId"], n_sims=_TEST_N_SIMS
    )
    assert result.n_playoff_teams == raw_fixture["settings"]["scheduleSettings"]["playoffTeamCount"]
    assert result.n_regular_weeks == raw_fixture["settings"]["scheduleSettings"]["matchupPeriodCount"]
    assert len(result.teams) == 10  # sim.params.mock_rosters' 10 mock teams
    for team in result.teams:
        assert team.recommendation


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


def test_get_playoff_planner_returns_404_for_an_uningested_league(client: TestClient) -> None:
    response = client.get("/league/424242/playoff-planner")
    assert response.status_code == 404


def test_get_playoff_planner_returns_409_when_no_roster_is_drafted(
    client: TestClient, pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]
) -> None:
    pre_draft = copy.deepcopy(raw_fixture)
    for team in pre_draft["teams"]:
        team["roster"] = {"entries": []}
    ingest_league(pg_conn, pre_draft, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    pg_conn.commit()

    response = client.get(
        f"/league/{pre_draft['id']}/playoff-planner", params={"season_id": pre_draft["seasonId"]}
    )
    assert response.status_code == 409


def test_get_playoff_planner_route_matches_a_direct_compute_call(
    client: TestClient, pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]
) -> None:
    league_id = raw_fixture["id"]
    season_id = raw_fixture["seasonId"]
    ingest_league(pg_conn, raw_fixture, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    pg_conn.commit()

    # Route has no n_sims query param (consistent with the sibling GET
    # routes -- /roster, /schedule, /draft-autopsy), so compare against the
    # same DEFAULT_N_SIMS the route itself uses, not the faster _TEST_N_SIMS
    # the rest of this file uses for standalone grading-logic checks.
    direct = compute_playoff_planner(pg_conn, league_id, season_id, n_sims=DEFAULT_N_SIMS)

    response = client.get(
        f"/league/{league_id}/playoff-planner",
        params={"season_id": season_id},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["league_id"] == league_id
    assert body["season_id"] == season_id
    assert body["seed"] == direct.seed
    assert len(body["teams"]) == len(direct.teams)
    assert len(body["seeding"]) == len(direct.seeding)
    assert len(body["bracket"]) == len(direct.bracket)

    direct_by_id = {t.team_id: t for t in direct.teams}
    for team_out in body["teams"]:
        direct_team = direct_by_id[team_out["team_id"]]
        assert team_out["weakest_slot"] == direct_team.weakest_slot
        assert team_out["recommendation"] == direct_team.recommendation
        assert len(team_out["slot_strengths"]) == len(direct_team.slot_strengths)


def test_get_playoff_planner_works_for_the_synthetic_league(
    client: TestClient, synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    response = client.get(
        f"/league/{synthetic_league_id}/playoff-planner",
        params={"season_id": raw_fixture["seasonId"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["teams"]) == 10
