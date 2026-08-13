"""Tests for ingest.parse against the real saved fixture (no network calls)
plus synthetic ESPN-shaped payloads for edge cases the real fixture doesn't
happen to exercise (e.g. a team with a drafted roster)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from ingest.errors import MissingProjectionError, RosterNotAvailableError
from ingest.models import LeagueSummary
from ingest.parse import (
    FIXTURES_DIR,
    build_team_params,
    load_fixture,
    parse_league_summary,
    parse_player_pool,
    parse_schedule,
    parse_scoring_table,
    parse_teams,
)
from ingest.slots import DEFAULT_POSITION_TO_SLOT
from sim.engine import PlayerParams, TeamParams

FIXTURE_PATH = FIXTURES_DIR / "league_raw_2026.json"


@pytest.fixture(scope="module")
def raw() -> dict[str, Any]:
    return load_fixture(FIXTURE_PATH)


@pytest.fixture(scope="module")
def summary(raw: dict[str, Any]) -> LeagueSummary:
    return parse_league_summary(raw)


# --------------------------------------------------------------------------
# Fixture loading
# --------------------------------------------------------------------------


def test_load_fixture_reads_real_file() -> None:
    data = load_fixture(FIXTURE_PATH)
    assert data["seasonId"] == 2026
    assert data["settings"]["name"]


# --------------------------------------------------------------------------
# Teams
# --------------------------------------------------------------------------


def test_parse_teams_returns_all_slots_sorted_by_id(raw: dict[str, Any]) -> None:
    teams = parse_teams(raw)
    assert [t.team_id for t in teams] == sorted(t.team_id for t in teams)
    assert len(teams) == raw["settings"]["size"]


def test_parse_teams_flags_unclaimed_slots(raw: dict[str, Any]) -> None:
    teams = parse_teams(raw)
    claimed = [t for t in teams if t.has_owner]
    unclaimed = [t for t in teams if not t.has_owner]
    # This fixture is now an 8-slot league, fully joined and drafted (see
    # docs/decisions.md) -- every team is claimed, so `unclaimed` is
    # correctly empty here. The "has genuinely unclaimed slots" case that
    # made this assertion meaningful pre-draft is covered on synthetic data
    # below instead, since the real fixture no longer exercises it.
    assert len(claimed) == raw["status"]["teamsJoined"]
    assert len(unclaimed) == len(teams) - len(claimed)


def test_parse_teams_flags_unclaimed_slots_on_synthetic_partial_league() -> None:
    """Synthetic payload exercising an unclaimed-slot league, since the real
    fixture is now fully joined and no longer covers this case (see the
    test above)."""
    synthetic_raw = {
        "teams": [
            {"id": 1, "name": "Claimed Team", "owners": ["member_aaaa"]},
            {"id": 2, "name": "Unclaimed Team", "owners": []},
        ]
    }
    teams = parse_teams(synthetic_raw)
    claimed = [t for t in teams if t.has_owner]
    unclaimed = [t for t in teams if not t.has_owner]
    assert len(claimed) == 1
    assert len(unclaimed) == 1


# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------


def test_parse_schedule_shape_and_symmetry(raw: dict[str, Any]) -> None:
    teams = parse_teams(raw)
    n_regular_weeks = raw["settings"]["scheduleSettings"]["matchupPeriodCount"]
    schedule = parse_schedule(raw, teams, n_regular_weeks)

    assert schedule.pairings.shape == (n_regular_weeks, len(teams))
    for week in schedule.pairings:
        np.testing.assert_array_equal(week[week], np.arange(len(teams)))
        assert not np.any(week == np.arange(len(teams)))


def test_parse_schedule_uses_matchup_period_not_scoring_period(raw: dict[str, Any]) -> None:
    """Regression guard for the scoringPeriodId/matchupPeriodId mixup CLAUDE.md
    warns about: every matchup entry parsed must come from matchupPeriodId,
    and the resulting week count must equal matchupPeriodCount, not
    finalScoringPeriod (which also counts playoff weeks)."""
    teams = parse_teams(raw)
    n_regular_weeks = raw["settings"]["scheduleSettings"]["matchupPeriodCount"]
    schedule = parse_schedule(raw, teams, n_regular_weeks)

    final_scoring_period = raw["status"]["finalScoringPeriod"]
    assert schedule.n_regular_weeks == n_regular_weeks
    assert schedule.n_regular_weeks < final_scoring_period  # playoffs extend past it


def test_parse_schedule_raises_on_incomplete_pairing() -> None:
    teams_raw = {
        "teams": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
        "schedule": [
            {"matchupPeriodId": 1, "home": {"teamId": 1}, "away": {"teamId": 2}},
            # week 2 missing entirely
        ],
    }
    teams = parse_teams(teams_raw)
    with pytest.raises(ValueError, match="missing a pairing"):
        parse_schedule(teams_raw, teams, n_regular_weeks=2)


# --------------------------------------------------------------------------
# Scoring settings / player pool
# --------------------------------------------------------------------------


def test_league_is_full_ppr(raw: dict[str, Any]) -> None:
    """This league scores 1 point per reception -- confirmed from its own
    scoringSettings, not assumed."""
    table = parse_scoring_table(raw)
    assert table.rate["53"] == pytest.approx(1.0)


def test_parse_player_pool_skips_known_bad_players(raw: dict[str, Any]) -> None:
    table = parse_scoring_table(raw)
    projections, skipped = parse_player_pool(raw, table)

    skipped_names = {p.name for p in skipped}
    # Verified by direct inspection of the fixture (see docs/decisions.md):
    # two players have no season-projection stat block at all, two have one
    # with appliedTotal == 0. James Conner is now rostered rather than a
    # free agent (the league has since drafted), but the pool now covers
    # rostered players too (see docs/decisions.md), so he's still found and
    # still correctly skipped for the same reason.
    assert {"Tyreek Hill", "Brandon Aiyuk"} <= skipped_names
    assert {"Ricky Pearsall", "James Conner"} <= skipped_names

    projected_names = {p.name for p in projections}
    assert projected_names.isdisjoint(skipped_names)

    # The pool is the union of free agents and every rostered player, not
    # just _freeAgents (which no longer includes drafted players at all).
    rostered_ids = {
        entry["playerPoolEntry"]["player"]["id"]
        for team in raw["teams"]
        for entry in team.get("roster", {}).get("entries", [])
    }
    free_agent_ids = {e["player"]["id"] for e in raw["_freeAgents"]}
    total_unique_players = len(free_agent_ids | rostered_ids)
    assert len(projections) + len(skipped) == total_unique_players


def test_parse_player_pool_means_are_positive(raw: dict[str, Any]) -> None:
    table = parse_scoring_table(raw)
    projections, _ = parse_player_pool(raw, table)
    assert projections  # non-empty
    for p in projections:
        assert p.mean_points_per_game > 0
        assert p.games_projected > 0
        assert p.default_position_id in DEFAULT_POSITION_TO_SLOT


# --------------------------------------------------------------------------
# League summary
# --------------------------------------------------------------------------


def test_parse_league_summary(summary: LeagueSummary, raw: dict[str, Any]) -> None:
    assert summary.league_id == raw["id"]
    assert summary.season_id == 2026
    # This fixture was refreshed after the league's real draft (see
    # docs/decisions.md) -- it is now an 8-team, fully-joined, drafted
    # league, not the earlier 12-team pre-draft snapshot.
    assert summary.is_drafted is True
    assert summary.size == 8
    assert summary.n_playoff_teams == 4
    assert summary.schedule.n_teams == summary.size
    assert len(summary.teams) == summary.size


# --------------------------------------------------------------------------
# build_team_params
# --------------------------------------------------------------------------


def test_build_team_params_raises_when_no_roster_exists() -> None:
    """Synthetic pre-draft-shaped payload (draftDetail.drafted False, empty
    roster.entries) -- build_team_params must refuse to invent a lineup
    rather than pull one from the free-agent pool. The real fixture no
    longer covers this case since the league has since drafted (see
    docs/decisions.md), so this uses the same synthetic-payload pattern as
    the other edge-case tests in this section."""
    synthetic_raw = {
        "draftDetail": {"drafted": False},
        "teams": [{"id": 1, "name": "Test Team", "roster": {"entries": []}}],
    }
    with pytest.raises(RosterNotAvailableError, match="no roster entries"):
        build_team_params(synthetic_raw, team_id=1, players_by_id={})


def test_build_team_params_raises_for_unknown_team(raw: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="no team with id"):
        build_team_params(raw, team_id=99999, players_by_id={})


def _synthetic_player(player_id: int) -> PlayerParams:
    return PlayerParams(player_id=player_id, mean=12.0, sd=5.0, availability=0.9)


def test_build_team_params_builds_starters_from_a_drafted_roster() -> None:
    """Synthetic, ESPN-shaped roster entries (this fixture has none, since
    its draft hasn't happened) exercising the success path: starting slots
    are kept, bench/IR are dropped, in the roster's given order."""
    synthetic_raw = {
        "draftDetail": {"drafted": True},
        "teams": [
            {
                "id": 1,
                "name": "Test Team",
                "roster": {
                    "entries": [
                        {"playerId": 100, "lineupSlotId": 0},  # QB, starting
                        {"playerId": 101, "lineupSlotId": 2},  # RB, starting
                        {"playerId": 102, "lineupSlotId": 20},  # Bench
                        {"playerId": 103, "lineupSlotId": 21},  # IR
                    ]
                },
            }
        ],
    }
    players_by_id = {pid: _synthetic_player(pid) for pid in (100, 101, 102, 103)}

    team = build_team_params(synthetic_raw, team_id=1, players_by_id=players_by_id)

    assert isinstance(team, TeamParams)
    assert team.team_id == 1
    assert team.name == "Test Team"
    assert {p.player_id for p in team.starters} == {100, 101}


def test_build_team_params_raises_on_starter_with_no_projection() -> None:
    synthetic_raw = {
        "teams": [
            {
                "id": 1,
                "name": "Test Team",
                "roster": {"entries": [{"playerId": 100, "lineupSlotId": 0}]},
            }
        ]
    }
    with pytest.raises(MissingProjectionError, match="no usable projection"):
        build_team_params(synthetic_raw, team_id=1, players_by_id={})


def test_build_team_params_raises_when_roster_is_all_bench(raw: dict[str, Any]) -> None:
    synthetic_raw = {
        "teams": [
            {
                "id": 1,
                "name": "Test Team",
                "roster": {"entries": [{"playerId": 100, "lineupSlotId": 20}]},
            }
        ]
    }
    players_by_id = {100: _synthetic_player(100)}
    with pytest.raises(RosterNotAvailableError, match="none in a starting"):
        build_team_params(synthetic_raw, team_id=1, players_by_id=players_by_id)
