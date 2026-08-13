"""SYNTHETIC DATA -- for pipeline validation only, not real team predictions.

This module exists because `fixtures/league_raw_2026.json` was genuinely
pre-draft through Phase 5 (every team's `roster.entries` empty,
`draftDetail.drafted` `False` -- see docs/decisions.md, Phase 1 and Phase 2
sections): with no real roster, `ingest.parse.build_team_params` correctly
raised `RosterNotAvailableError` for every one of this league's real teams,
and this module does not work around that. The real league has since
drafted, but this module is kept and still used for pipeline
validation/testing (e.g. the API's synthetic league) independent of that --
it never reads or depends on any real team's actual roster.

What this module builds instead: a small mock league of fabricated team
rosters, assembled by a simple simulated snake draft over the fixture's real
free-agent pool. Every individual player's `PlayerParams` (mean,
sd, availability) is real, derived by `sim.params.derive` exactly as it would
be for an actual roster. What is fake is *which players end up on which
team* -- these groupings do not correspond to any real manager, any actual
draft result, or any prediction about this league's real teams.

This exists for exactly one purpose: to exercise `sim.engine.TeamParams`,
`sim.engine.LeagueParams` and `simulate_seasons()` end to end while the real
league has no rosters, so the params-derivation pipeline can be sanity
checked (see sim/params/validate.py) before a real post-draft fixture exists.
Team names are deliberately unmistakable ("Mock Team A", ...) so this output
can never be confused with a real forecast. Once a post-draft fixture is
available, `ingest.parse.build_team_params` replaces this module for real
usage -- this module is not meant to be wired into any user-facing feature.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ingest.models import PlayerProjection
from ingest.slots import starting_slot_counts
from sim.engine import LeagueParams, PlayerParams, TeamParams, round_robin_schedule

_MOCK_TEAM_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# ESPN lineupSlotId -> defaultPositionId, for the *starting* slots this
# module knows how to fill. The inverse of ingest.slots.DEFAULT_POSITION_TO_SLOT,
# restricted to the slot ids this league's own rosterSettings.lineupSlotCounts
# actually uses with a non-flex, single-position requirement.
_SLOT_TO_DEFAULT_POSITION: dict[int, int] = {0: 1, 2: 2, 4: 3, 6: 4, 16: 16, 17: 5}

_FLEX_SLOT_ID = 23
_FLEX_ELIGIBLE_POSITIONS = frozenset({2, 3, 4})  # RB, WR, TE

# Draft-round ordering: which starting slot gets filled in which round of the
# mock snake draft, one entry per round. This is an explicit, documented
# realism assumption (not a number that feeds sd/mean/availability) --
# alternating WR/RB early (a common real "Zero RB"-adjacent redraft pattern)
# rather than drafting all of one position back to back keeps the very best
# overall player at each spiky position (WR, RB) from concentrating onto a
# single mock team, and leaves QB/D-ST/K for last as in most real drafts. It
# has no effect on any individual player's PlayerParams, only on which fake
# team a real player lands on. Validated at runtime against this league's
# own settings.rosterSettings.lineupSlotCounts below rather than assumed.
_ROUND_SEQUENCE: tuple[int, ...] = (4, 2, 4, 2, 6, _FLEX_SLOT_ID, 0, 16, 17)  # WR,RB,WR,RB,TE,FLEX,QB,D/ST,K


def _draft_rounds(raw: Mapping[str, Any]) -> list[int]:
    """One lineupSlotId per round of the mock draft (`_ROUND_SEQUENCE`),
    cross-checked against this league's own starting lineup counts
    (settings.rosterSettings) so a lineup shape this module wasn't written
    for fails loudly instead of drafting the wrong number of a position.
    """
    roster_settings = raw.get("settings", {}).get("rosterSettings", {})
    lineup_slot_counts = roster_settings.get("lineupSlotCounts")
    if not lineup_slot_counts:
        raise ValueError("fixture has no settings.rosterSettings.lineupSlotCounts")

    counts = starting_slot_counts(lineup_slot_counts)
    expected_counts: dict[int, int] = {}
    for slot_id in _ROUND_SEQUENCE:
        expected_counts[slot_id] = expected_counts.get(slot_id, 0) + 1

    if counts != expected_counts:
        raise ValueError(
            f"this league's starting lineup {counts} does not match the "
            f"mock draft's hardcoded _ROUND_SEQUENCE {expected_counts} -- "
            "the lineup shape changed; update _ROUND_SEQUENCE (and "
            "_SLOT_TO_DEFAULT_POSITION if a new position was introduced)"
        )

    return list(_ROUND_SEQUENCE)


def _eligible(slot_id: int, projection: PlayerProjection) -> bool:
    if slot_id == _FLEX_SLOT_ID:
        return (
            projection.default_position_id in _FLEX_ELIGIBLE_POSITIONS
            and _FLEX_SLOT_ID in projection.eligible_slots
        )
    return projection.default_position_id == _SLOT_TO_DEFAULT_POSITION[slot_id]


def draft_mock_rosters(
    raw: Mapping[str, Any],
    player_pool: tuple[PlayerProjection, ...],
    n_mock_teams: int = 10,
) -> tuple[tuple[tuple[int, int], ...], ...]:
    """Run the mock snake draft and return, per team index, a tuple of
    (player_id, lineup_slot_id) -- the roster *assignments*, before any
    PlayerParams are resolved.

    Pulled out of `build_mock_league` as its own function so the exact same
    draft (not a reimplementation of it) can back other consumers that need
    the assignment's lineupSlotId too -- e.g. building a synthetic raw ESPN
    payload with populated `teams[].roster.entries` for a Phase 4 API
    verification league, which needs slot ids that `build_mock_league`
    itself discards once it has resolved each pick to a PlayerParams.
    """
    if n_mock_teams % 2 != 0:
        raise ValueError(f"n_mock_teams must be even for round_robin_schedule, got {n_mock_teams}")
    if n_mock_teams > len(_MOCK_TEAM_LETTERS):
        raise ValueError(f"n_mock_teams too large to letter uniquely: {n_mock_teams}")

    rounds = _draft_rounds(raw)

    drafted: set[int] = set()
    rosters: list[list[tuple[int, int]]] = [[] for _ in range(n_mock_teams)]

    for round_index, slot_id in enumerate(rounds):
        # True continuous snake draft: direction alternates by round, and the
        # team that picked last in a round picks first in the next.
        order = range(n_mock_teams) if round_index % 2 == 0 else range(n_mock_teams - 1, -1, -1)
        for team_index in order:
            candidates = [
                p for p in player_pool if p.player_id not in drafted and _eligible(slot_id, p)
            ]
            if not candidates:
                raise ValueError(
                    f"mock draft ran out of eligible players for lineupSlotId="
                    f"{slot_id} in round {round_index} -- player pool too small "
                    "for n_mock_teams"
                )
            best = max(candidates, key=lambda p: p.mean_points_per_game)
            drafted.add(best.player_id)
            rosters[team_index].append((best.player_id, slot_id))

    return tuple(tuple(roster) for roster in rosters)


def build_mock_league(
    raw: Mapping[str, Any],
    player_pool: tuple[PlayerProjection, ...],
    players_by_id: Mapping[int, PlayerParams],
    n_mock_teams: int = 10,
) -> LeagueParams:
    """Build a SYNTHETIC LeagueParams by snake-drafting real players (with
    real, derived PlayerParams) onto `n_mock_teams` fake rosters.

    `n_mock_teams` defaults to 10 to match the "10-team league" sanity check
    in PLAN.md's Phase 2 section, not because this league has 10 teams (it
    has 12 slots, 10 claimed, and none of that is relevant here since no
    roster in this mock league corresponds to any real team).

    Regular-season week count and playoff field size are read from this
    fixture's own real `scheduleSettings` -- those are genuine league
    structure, independent of the roster question, so there is no reason to
    fabricate them. `players_by_id` must already hold real PlayerParams for
    every player in `player_pool` (see sim.params.derive).
    """
    missing = [p.player_id for p in player_pool if p.player_id not in players_by_id]
    if missing:
        raise ValueError(
            f"{len(missing)} players in player_pool have no entry in "
            f"players_by_id, e.g. player_id={missing[0]}"
        )

    rosters = draft_mock_rosters(raw, player_pool, n_mock_teams)

    schedule_settings = raw["settings"]["scheduleSettings"]
    n_regular_weeks = schedule_settings["matchupPeriodCount"]
    n_playoff_teams = schedule_settings["playoffTeamCount"]

    teams = tuple(
        TeamParams(
            team_id=i,
            name=f"Mock Team {_MOCK_TEAM_LETTERS[i]} (SYNTHETIC -- validation only)",
            starters=tuple(players_by_id[pid] for pid, _slot_id in roster),
        )
        for i, roster in enumerate(rosters)
    )

    return LeagueParams(
        teams=teams,
        schedule=round_robin_schedule(n_teams=n_mock_teams, n_weeks=n_regular_weeks),
        n_playoff_teams=n_playoff_teams,
    )
