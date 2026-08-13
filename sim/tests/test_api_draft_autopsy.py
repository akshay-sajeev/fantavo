"""Tests for sim.api.draft_autopsy_view and the GET /league/{id}/draft-autopsy
route.

Draft autopsy is only demonstrable against a league with a real completed
draft -- fixtures/league_raw_2026.json's real league (league_id=885686492).
The SYNTHETIC validation league's mock draft fabricates rosters directly
with no pick sequence (`draftDetail.picks == []`), so it is used here only
to verify the 409 "no draft to autopsy" path, not the grading logic itself.

Most of the grading-logic coverage runs directly against the in-memory
fixture dict via `_compute_from_raw` (no Postgres needed, fast); the
Postgres-backed tests cover the route/`load_raw_payload` wiring and the two
error paths a real HTTP client would see.
"""

from __future__ import annotations

import copy
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import DEFAULT_TEST_DSN, ingest_league
from ingest.errors import DraftNotAvailableError
from sim.api import app as app_module
from sim.api.draft_autopsy_view import (
    _BENCH_BUCKET,
    _GRADED_POSITIONS,
    _compute_from_raw,
    compute_draft_autopsy,
)

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)


# --------------------------------------------------------------------------
# Grading logic, directly against the fixture dict -- no Postgres needed.
# --------------------------------------------------------------------------


def test_compute_from_raw_grades_every_real_pick(raw_fixture: dict[str, Any]) -> None:
    autopsy = _compute_from_raw(raw_fixture, raw_fixture["id"], raw_fixture["seasonId"])

    assert autopsy.rank_source
    assert len(autopsy.teams) == 8  # this league's real, current team count

    all_picks = [p for team in autopsy.teams for p in team.picks]
    assert len(all_picks) == 128  # this fixture's real draftDetail.picks count
    assert {p.overall_pick_number for p in all_picks} == set(range(1, 129))

    for team in autopsy.teams:
        assert len(team.picks) == 16  # 128 picks / 8 teams
        # bucketed (QB/RB/WR/TE/Bench) + unbucketed (K, D/ST) must cover
        # every pick exactly once.
        bucketed = sum(1 for p in team.picks if p.grade_bucket is not None)
        unbucketed = sum(1 for p in team.picks if p.grade_bucket is None)
        assert bucketed + unbucketed == 16
        assert bucketed == 14  # 1 QB + 2 RB + 2 WR + 1 TE + 1 FLEX + 7 BE
        assert unbucketed == 2  # 1 K + 1 D/ST

        # best_pick/worst_pick are real members of this team's own picks,
        # and the extremes of value_gap across them.
        assert team.best_pick in team.picks
        assert team.worst_pick in team.picks
        gaps = [p.value_gap for p in team.picks]
        assert team.best_pick.value_gap == max(gaps)
        assert team.worst_pick.value_gap == min(gaps)

        # Every bucket present sums to a real, positive pick count and both
        # its own and the league average are real floats, not placeholders.
        seen_buckets = {g.position for g in team.position_grades}
        assert seen_buckets <= {*_GRADED_POSITIONS, _BENCH_BUCKET}
        for grade in team.position_grades:
            assert grade.pick_count > 0

        assert team.structural_finding  # never empty for a real drafted team


def test_value_gap_sign_convention(raw_fixture: dict[str, Any]) -> None:
    """A pick with a positive value_gap took a same-position player ranked
    *better* than the best remaining alternative (a correct-process pick); a
    negative value_gap means a better-ranked alternative at the same
    position was still on the board and was passed over."""
    autopsy = _compute_from_raw(raw_fixture, raw_fixture["id"], raw_fixture["seasonId"])
    for team in autopsy.teams:
        for pick in team.picks:
            if pick.alternative_player_rank is None:
                assert pick.value_gap == 0.0
                continue
            expected_gap = float(pick.alternative_player_rank - pick.player_rank)
            assert pick.value_gap == expected_gap
            if pick.value_gap > 0:
                assert pick.player_rank < pick.alternative_player_rank
            elif pick.value_gap < 0:
                assert pick.player_rank > pick.alternative_player_rank


def test_alternative_is_never_the_picked_player_or_already_drafted(
    raw_fixture: dict[str, Any],
) -> None:
    autopsy = _compute_from_raw(raw_fixture, raw_fixture["id"], raw_fixture["seasonId"])
    all_picks = sorted(
        (p for team in autopsy.teams for p in team.picks), key=lambda p: p.overall_pick_number
    )
    drafted_before: set[int] = set()
    for pick in all_picks:
        if pick.alternative_player_id is not None:
            assert pick.alternative_player_id != pick.player_id
            assert pick.alternative_player_id not in drafted_before
        if pick.best_overall_available_player_id is not None:
            assert pick.best_overall_available_player_id != pick.player_id
            assert pick.best_overall_available_player_id not in drafted_before
        drafted_before.add(pick.player_id)


def test_alternative_is_always_the_same_position_as_the_pick(raw_fixture: dict[str, Any]) -> None:
    autopsy = _compute_from_raw(raw_fixture, raw_fixture["id"], raw_fixture["seasonId"])
    players_by_id = {
        p["id"]: p
        for p in (e["player"] for e in raw_fixture["_freeAgents"])
    }
    for team in raw_fixture["teams"]:
        for entry in team["roster"]["entries"]:
            player = entry["playerPoolEntry"]["player"]
            players_by_id[player["id"]] = player

    for team in autopsy.teams:
        for pick in team.picks:
            if pick.alternative_player_id is None:
                continue
            picked_pos = players_by_id[pick.player_id]["defaultPositionId"]
            alt_pos = players_by_id[pick.alternative_player_id]["defaultPositionId"]
            assert picked_pos == alt_pos


def test_bench_bucket_uses_draft_time_slot_not_current_roster(raw_fixture: dict[str, Any]) -> None:
    """Some players have moved (traded/waived) since the draft (see
    docs/decisions.md Phase 7) -- grade_bucket must reflect the pick's own
    lineupSlotId at draft time, not the player's current roster slot."""
    autopsy = _compute_from_raw(raw_fixture, raw_fixture["id"], raw_fixture["seasonId"])
    raw_picks_by_number = {
        p["overallPickNumber"]: p for p in raw_fixture["draftDetail"]["picks"]
    }
    for team in autopsy.teams:
        for pick in team.picks:
            raw_pick = raw_picks_by_number[pick.overall_pick_number]
            if raw_pick["lineupSlotId"] == 20:
                assert pick.grade_bucket == _BENCH_BUCKET


def test_compute_from_raw_raises_for_a_payload_with_no_draft(raw_fixture: dict[str, Any]) -> None:
    pre_draft = copy.deepcopy(raw_fixture)
    pre_draft["draftDetail"]["drafted"] = False
    pre_draft["draftDetail"]["picks"] = []
    with pytest.raises(DraftNotAvailableError):
        _compute_from_raw(pre_draft, pre_draft["id"], pre_draft["seasonId"])


def test_compute_from_raw_raises_for_empty_picks_even_if_drafted_flag_is_true(
    raw_fixture: dict[str, Any],
) -> None:
    """Exactly the SYNTHETIC validation league's shape:
    scripts/ingest_synthetic_league.py sets drafted=True but picks=[]."""
    synthetic_shaped = copy.deepcopy(raw_fixture)
    synthetic_shaped["draftDetail"]["drafted"] = True
    synthetic_shaped["draftDetail"]["picks"] = []
    with pytest.raises(DraftNotAvailableError):
        _compute_from_raw(synthetic_shaped, synthetic_shaped["id"], synthetic_shaped["seasonId"])


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


def test_get_draft_autopsy_returns_404_for_an_uningested_league(client: TestClient) -> None:
    response = client.get("/league/424242/draft-autopsy")
    assert response.status_code == 404


def test_get_draft_autopsy_returns_409_for_the_synthetic_league(
    client: TestClient, synthetic_league_id: int
) -> None:
    """The SYNTHETIC validation league's mock draft never records a pick
    sequence (see scripts/ingest_synthetic_league.py and this module's own
    docstring) -- this is the load-bearing 409 case PLAN.md's Phase 7 task
    description calls out explicitly."""
    response = client.get(f"/league/{synthetic_league_id}/draft-autopsy")
    assert response.status_code == 409
    assert "draft" in response.json()["detail"].lower()


def test_get_draft_autopsy_route_matches_a_direct_compute_call(
    client: TestClient, pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]
) -> None:
    league_id = raw_fixture["id"]
    season_id = raw_fixture["seasonId"]
    ingest_league(pg_conn, raw_fixture, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    pg_conn.commit()

    direct = compute_draft_autopsy(pg_conn, league_id, season_id)

    response = client.get(f"/league/{league_id}/draft-autopsy", params={"season_id": season_id})
    assert response.status_code == 200
    body = response.json()

    assert body["league_id"] == league_id
    assert body["season_id"] == season_id
    assert body["rank_source"] == direct.rank_source
    assert len(body["teams"]) == len(direct.teams)

    direct_by_id = {t.team_id: t for t in direct.teams}
    for team_out in body["teams"]:
        direct_team = direct_by_id[team_out["team_id"]]
        assert len(team_out["picks"]) == len(direct_team.picks)
        assert team_out["best_pick"]["player_id"] == direct_team.best_pick.player_id
        assert team_out["worst_pick"]["player_id"] == direct_team.worst_pick.player_id
        assert team_out["structural_finding"] == direct_team.structural_finding
