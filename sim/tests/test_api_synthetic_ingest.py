"""Tests for scripts.ingest_synthetic_league -- no Postgres required. These
exercise the pure payload-construction and parsing round-trip; DB persistence
itself is covered by sim/tests/test_api_params_loader.py via the
`synthetic_league_id` fixture (sim/tests/conftest.py).
"""

from __future__ import annotations

from typing import Any

from ingest.parse import (
    build_team_params,
    parse_player_pool,
    parse_schedule,
    parse_scoring_table,
    parse_teams,
)
from ingest.slots import NON_STARTING_SLOTS
from scripts.ingest_synthetic_league import (
    N_MOCK_TEAMS,
    SYNTHETIC_LEAGUE_ID,
    build_synthetic_raw_payload,
)
from sim.engine import round_robin_schedule
from sim.params.derive import derive_player_params_pool
from sim.params.mock_rosters import build_mock_league
from sim.params.variance import fit_position_cv


def test_synthetic_payload_has_the_expected_shape(raw_fixture: dict[str, Any]) -> None:
    payload = build_synthetic_raw_payload(raw_fixture)

    assert payload["id"] == SYNTHETIC_LEAGUE_ID
    assert payload["id"] < 0  # unmistakable: no real ESPN league id is negative
    assert payload["seasonId"] == raw_fixture["seasonId"]
    assert payload["draftDetail"]["drafted"] is True
    assert len(payload["teams"]) == N_MOCK_TEAMS
    for team in payload["teams"]:
        assert "SYNTHETIC" in team["name"]
        entries = team["roster"]["entries"]
        assert len(entries) == 9  # QB, RB, RB, WR, WR, TE, FLEX, D/ST, K
        for entry in entries:
            assert entry["lineupSlotId"] not in NON_STARTING_SLOTS


def test_synthetic_payload_drafts_no_player_twice(raw_fixture: dict[str, Any]) -> None:
    payload = build_synthetic_raw_payload(raw_fixture)
    all_ids = [
        entry["playerId"] for team in payload["teams"] for entry in team["roster"]["entries"]
    ]
    assert len(all_ids) == len(set(all_ids))


def test_synthetic_payload_parses_through_the_real_ingest_pipeline(
    raw_fixture: dict[str, Any],
) -> None:
    """The exact functions ingest.db.ingest_league calls internally must
    accept this synthetic payload without any special-casing."""
    payload = build_synthetic_raw_payload(raw_fixture)

    scoring_table = parse_scoring_table(payload)
    player_pool, skipped = parse_player_pool(payload, scoring_table)
    # Same real _freeAgents block as the source fixture, so the same
    # skip/keep split applies.
    real_scoring_table = parse_scoring_table(raw_fixture)
    real_pool, real_skipped = parse_player_pool(raw_fixture, real_scoring_table)
    assert len(player_pool) == len(real_pool)
    assert len(skipped) == len(real_skipped)

    teams = parse_teams(payload)
    assert len(teams) == N_MOCK_TEAMS
    schedule = parse_schedule(payload, teams, payload["settings"]["scheduleSettings"]["matchupPeriodCount"])
    assert schedule.n_teams == N_MOCK_TEAMS


def test_synthetic_payload_schedule_matches_round_robin_schedule(
    raw_fixture: dict[str, Any],
) -> None:
    payload = build_synthetic_raw_payload(raw_fixture)
    teams = parse_teams(payload)
    n_regular_weeks = payload["settings"]["scheduleSettings"]["matchupPeriodCount"]
    schedule = parse_schedule(payload, teams, n_regular_weeks)

    expected = round_robin_schedule(n_teams=N_MOCK_TEAMS, n_weeks=n_regular_weeks)
    assert (schedule.pairings == expected).all()


def test_synthetic_payload_rosters_match_build_mock_league(raw_fixture: dict[str, Any]) -> None:
    """The synthetic raw payload's teams[].roster.entries must reconstruct,
    through the real ingest.parse.build_team_params path, the exact same
    rosters sim.params.mock_rosters.build_mock_league builds directly --
    proof the payload is a faithful ESPN-shaped encoding of the same draft,
    not a divergent second implementation of it.
    """
    payload = build_synthetic_raw_payload(raw_fixture)

    scoring_table = parse_scoring_table(raw_fixture)
    player_pool, _ = parse_player_pool(raw_fixture, scoring_table)
    position_cv = fit_position_cv(raw_fixture)
    players_by_id = derive_player_params_pool(player_pool, position_cv)

    expected_league = build_mock_league(raw_fixture, player_pool, players_by_id, n_mock_teams=10)

    for team_id in range(N_MOCK_TEAMS):
        rebuilt = build_team_params(payload, team_id, players_by_id)
        expected_team = expected_league.teams[team_id]
        assert rebuilt.team_id == expected_team.team_id
        assert [p.player_id for p in rebuilt.starters] == [
            p.player_id for p in expected_team.starters
        ]
