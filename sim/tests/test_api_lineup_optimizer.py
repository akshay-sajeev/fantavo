"""Tests for sim.api.lineup_optimizer_view and the GET
/league/{id}/lineup-optimizer/{team_id} route.

Grading-logic coverage runs directly against `_compute_from_raw` with the
in-memory fixture dict (fast, no Postgres) -- same fast-unit-tests-plus-
thin-integration-test split `sim.api.draft_autopsy_view` /
`sim.api.playoff_planner_view` already established.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import ingest_league
from ingest.parse import parse_player_pool, parse_scoring_table, parse_teams
from ingest.slots import starting_slot_counts
from sim.api import app as app_module
from sim.api.lineup_optimizer_view import (
    DEFAULT_SEASON_N_SIMS,
    DEFAULT_WEEKLY_N_SIMS,
    UnknownTeamError,
    _compute_from_raw,
    _generate_candidates,
    _load_full_roster,
    _slot_instances,
    compute_lineup_optimizer,
)
from sim.params.derive import derive_player_params_pool
from sim.params.variance import fit_position_cv
from sim.tests.conftest import TEST_DSN

# Small enough to keep unit tests fast (19 candidates x this many sims each,
# twice -- weekly AND season); large enough that floor/title-probability
# comparisons aren't dominated by sampling noise for a sanity check like
# "safest is never worse than current."
_TEST_WEEKLY_N_SIMS = 2_000
_TEST_SEASON_N_SIMS = 500


def _first_team_id(raw: dict[str, Any]) -> int:
    ids = [int(t["id"]) for t in raw["teams"] if t.get("roster", {}).get("entries")]
    return min(ids)


# --------------------------------------------------------------------------
# Candidate generation -- the "real decision points" search space itself.
# --------------------------------------------------------------------------


def test_generate_candidates_are_all_single_slot_swaps(raw_fixture: dict[str, Any]) -> None:
    """Every non-baseline candidate must differ from the baseline in
    EXACTLY one slot instance -- the module's whole documented search-space
    scoping rests on this being true, not just asserted in a docstring."""
    raw = raw_fixture
    team_id = _first_team_id(raw)

    scoring_table = parse_scoring_table(raw)
    player_pool, _ = parse_player_pool(raw, scoring_table)
    projections_by_id = {p.player_id: p for p in player_pool}
    position_cv = fit_position_cv(raw)
    players_by_id = derive_player_params_pool(player_pool, position_cv)

    slot_requirements = starting_slot_counts(raw["settings"]["rosterSettings"]["lineupSlotCounts"])
    slot_instances = _slot_instances(slot_requirements)

    roster, entries = _load_full_roster(raw, team_id, players_by_id, projections_by_id)
    from sim.api.lineup_optimizer_view import _current_lineup_player_ids

    baseline_ids = _current_lineup_player_ids(entries, slot_instances)
    candidates = _generate_candidates(slot_instances, baseline_ids, roster)

    assert candidates[0].player_ids == baseline_ids
    for candidate in candidates[1:]:
        diff_positions = [
            i for i, pid in enumerate(candidate.player_ids) if pid != baseline_ids[i]
        ]
        assert len(diff_positions) == 1, "every generated candidate must be a single-slot swap"
        slot_id = slot_instances[diff_positions[0]]
        swapped_in_player = candidate.player_ids[diff_positions[0]]
        # The swapped-in player must be a real bench player (not already a
        # baseline starter) who is actually eligible for that exact slot --
        # never inferred from listed position (CLAUDE.md).
        assert swapped_in_player not in baseline_ids
        assert slot_id in roster[swapped_in_player].eligible_slots


def test_generate_candidates_includes_no_duplicate_player_within_a_lineup(
    raw_fixture: dict[str, Any],
) -> None:
    raw = raw_fixture
    team_id = _first_team_id(raw)
    scoring_table = parse_scoring_table(raw)
    player_pool, _ = parse_player_pool(raw, scoring_table)
    projections_by_id = {p.player_id: p for p in player_pool}
    position_cv = fit_position_cv(raw)
    players_by_id = derive_player_params_pool(player_pool, position_cv)
    slot_requirements = starting_slot_counts(raw["settings"]["rosterSettings"]["lineupSlotCounts"])
    slot_instances = _slot_instances(slot_requirements)
    roster, entries = _load_full_roster(raw, team_id, players_by_id, projections_by_id)
    from sim.api.lineup_optimizer_view import _current_lineup_player_ids

    baseline_ids = _current_lineup_player_ids(entries, slot_instances)
    candidates = _generate_candidates(slot_instances, baseline_ids, roster)
    for candidate in candidates:
        assert len(set(candidate.player_ids)) == len(candidate.player_ids)


# --------------------------------------------------------------------------
# Full computation, directly against the fixture dict -- no Postgres needed.
# --------------------------------------------------------------------------


def test_current_lineup_matches_real_ingested_starters(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    team_id = _first_team_id(raw)
    result = _compute_from_raw(
        raw,
        raw["id"],
        raw["seasonId"],
        team_id,
        weekly_n_sims=_TEST_WEEKLY_N_SIMS,
        season_n_sims=_TEST_SEASON_N_SIMS,
        seed=12345,
    )

    raw_team = next(t for t in raw["teams"] if t["id"] == team_id)
    starter_ids = {
        e["playerId"] for e in raw_team["roster"]["entries"] if e["lineupSlotId"] not in (20, 21)
    }
    assert {a.player_id for a in result.current.assignments} == starter_ids
    assert not any(a.is_swap for a in result.current.assignments)
    assert result.current.label == "Current"
    assert len(result.current.assignments) == 9  # 1 QB/2 RB/2 WR/1 TE/1 FLEX/1 D-ST/1 K


def test_safest_floor_is_never_worse_than_current(raw_fixture: dict[str, Any]) -> None:
    """Safest is chosen by argmax over floor across baseline + every
    single-slot swap -- the baseline is always in that set, so safest can
    never be strictly worse than current."""
    raw = raw_fixture
    team_id = _first_team_id(raw)
    result = _compute_from_raw(
        raw,
        raw["id"],
        raw["seasonId"],
        team_id,
        weekly_n_sims=_TEST_WEEKLY_N_SIMS,
        season_n_sims=_TEST_SEASON_N_SIMS,
        seed=12345,
    )
    assert result.safest.weekly_floor >= result.current.weekly_floor - 1e-9


def test_highest_upside_title_probability_is_never_worse_than_current(
    raw_fixture: dict[str, Any],
) -> None:
    raw = raw_fixture
    team_id = _first_team_id(raw)
    result = _compute_from_raw(
        raw,
        raw["id"],
        raw["seasonId"],
        team_id,
        weekly_n_sims=_TEST_WEEKLY_N_SIMS,
        season_n_sims=_TEST_SEASON_N_SIMS,
        seed=12345,
    )
    assert result.highest_upside.title_probability >= result.current.title_probability - 1e-9


def test_every_lineup_projection_has_a_sane_weekly_range(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    team_id = _first_team_id(raw)
    result = _compute_from_raw(
        raw,
        raw["id"],
        raw["seasonId"],
        team_id,
        weekly_n_sims=_TEST_WEEKLY_N_SIMS,
        season_n_sims=_TEST_SEASON_N_SIMS,
        seed=12345,
    )
    for projection in (result.current, result.safest, result.highest_upside):
        assert projection.weekly_floor <= projection.weekly_ceiling
        assert projection.weekly_floor <= projection.weekly_mean <= projection.weekly_ceiling
        assert 0.0 <= projection.title_probability <= 1.0
        assert 0.0 <= projection.playoff_probability <= 1.0
        assert projection.title_probability <= projection.playoff_probability + 1e-9
        assert sum(projection.finish_distribution) == pytest.approx(1.0, abs=1e-6)


def test_safest_and_upside_lineups_differ_from_current_by_at_most_one_slot(
    raw_fixture: dict[str, Any],
) -> None:
    """Both are drawn from the single-slot-swap search space (or the
    baseline itself) -- never a multi-slot reshuffle."""
    raw = raw_fixture
    team_id = _first_team_id(raw)
    result = _compute_from_raw(
        raw,
        raw["id"],
        raw["seasonId"],
        team_id,
        weekly_n_sims=_TEST_WEEKLY_N_SIMS,
        season_n_sims=_TEST_SEASON_N_SIMS,
        seed=12345,
    )
    for projection in (result.safest, result.highest_upside):
        n_swaps = sum(1 for a in projection.assignments if a.is_swap)
        assert n_swaps <= 1


def test_result_is_deterministic_for_a_fixed_seed(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    team_id = _first_team_id(raw)
    kwargs: dict[str, Any] = {
        "weekly_n_sims": _TEST_WEEKLY_N_SIMS,
        "season_n_sims": _TEST_SEASON_N_SIMS,
        "seed": 999,
    }
    first = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, **kwargs)
    second = _compute_from_raw(raw, raw["id"], raw["seasonId"], team_id, **kwargs)
    assert first.safest.weekly_floor == second.safest.weekly_floor
    assert first.highest_upside.title_probability == second.highest_upside.title_probability
    assert [a.player_id for a in first.safest.assignments] == [
        a.player_id for a in second.safest.assignments
    ]


def test_unknown_team_id_raises(raw_fixture: dict[str, Any]) -> None:
    raw = raw_fixture
    with pytest.raises(UnknownTeamError):
        _compute_from_raw(
            raw,
            raw["id"],
            raw["seasonId"],
            team_id=-999999,
            weekly_n_sims=_TEST_WEEKLY_N_SIMS,
            season_n_sims=_TEST_SEASON_N_SIMS,
            seed=1,
        )


def test_every_real_team_produces_a_full_result(raw_fixture: dict[str, Any]) -> None:
    """Runs the full search for every real team in the league, not just one
    -- guards against a bug that only shows up for a roster shape (bench
    composition, flex depth) a single hand-picked team happens not to hit."""
    raw = raw_fixture
    teams = parse_teams(raw)
    for team in teams:
        result = _compute_from_raw(
            raw,
            raw["id"],
            raw["seasonId"],
            team.team_id,
            weekly_n_sims=_TEST_WEEKLY_N_SIMS,
            season_n_sims=_TEST_SEASON_N_SIMS,
            seed=42,
        )
        assert result.team_id == team.team_id
        assert result.n_candidates_considered >= 1
        assert len(result.current.assignments) == 9


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


def test_get_lineup_optimizer_returns_404_for_an_uningested_league(client: TestClient) -> None:
    response = client.get("/league/424242/lineup-optimizer/1")
    assert response.status_code == 404


def test_get_lineup_optimizer_returns_409_when_no_roster_is_drafted(
    client: TestClient, pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]
) -> None:
    pre_draft = copy.deepcopy(raw_fixture)
    for team in pre_draft["teams"]:
        team["roster"] = {"entries": []}
    ingest_league(pg_conn, pre_draft, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    pg_conn.commit()

    response = client.get(
        f"/league/{pre_draft['id']}/lineup-optimizer/{pre_draft['teams'][0]['id']}",
        params={"season_id": pre_draft["seasonId"]},
    )
    assert response.status_code == 409


def test_get_lineup_optimizer_returns_404_for_an_unknown_team(
    client: TestClient, pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]
) -> None:
    ingest_league(pg_conn, raw_fixture, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    pg_conn.commit()

    response = client.get(
        f"/league/{raw_fixture['id']}/lineup-optimizer/-999999",
        params={"season_id": raw_fixture["seasonId"]},
    )
    assert response.status_code == 404


def test_get_lineup_optimizer_route_matches_a_direct_compute_call(
    client: TestClient, pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]
) -> None:
    league_id = raw_fixture["id"]
    season_id = raw_fixture["seasonId"]
    team_id = _first_team_id(raw_fixture)
    ingest_league(pg_conn, raw_fixture, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    pg_conn.commit()

    # Route has no n_sims query params (consistent with the sibling GET
    # routes -- /roster, /schedule, /playoff-planner), so compare against
    # the same DEFAULT_WEEKLY_N_SIMS/DEFAULT_SEASON_N_SIMS the route itself
    # uses, not the faster _TEST_* sims the rest of this file uses for
    # standalone logic checks.
    direct = compute_lineup_optimizer(
        pg_conn,
        league_id,
        season_id,
        team_id,
        weekly_n_sims=DEFAULT_WEEKLY_N_SIMS,
        season_n_sims=DEFAULT_SEASON_N_SIMS,
        seed=777,
    )

    response = client.get(
        f"/league/{league_id}/lineup-optimizer/{team_id}",
        params={"season_id": season_id, "seed": 777},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["league_id"] == league_id
    assert body["team_id"] == team_id
    assert body["seed"] == direct.seed == 777
    assert body["n_candidates_considered"] == direct.n_candidates_considered
    assert len(body["current"]["assignments"]) == len(direct.current.assignments)
    assert body["safest"]["weekly_floor"] == pytest.approx(direct.safest.weekly_floor)
    assert body["highest_upside"]["title_probability"] == pytest.approx(
        direct.highest_upside.title_probability
    )


def test_get_lineup_optimizer_default_seed_is_deterministic_per_team(
    client: TestClient, pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]
) -> None:
    """No explicit seed passed -- two requests for the same league/season/team
    must still agree, via sim.api.seeds.lineup_optimizer_seed."""
    league_id = raw_fixture["id"]
    season_id = raw_fixture["seasonId"]
    team_id = _first_team_id(raw_fixture)
    ingest_league(pg_conn, raw_fixture, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    pg_conn.commit()

    first = client.get(
        f"/league/{league_id}/lineup-optimizer/{team_id}", params={"season_id": season_id}
    )
    second = client.get(
        f"/league/{league_id}/lineup-optimizer/{team_id}", params={"season_id": season_id}
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["seed"] == second.json()["seed"]
    assert first.json()["safest"]["weekly_floor"] == pytest.approx(
        second.json()["safest"]["weekly_floor"]
    )
