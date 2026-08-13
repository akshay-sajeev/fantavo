"""Parse a saved ESPN fixture into the intermediate types in ingest.models,
and (where the fixture has a real roster) into sim.engine.TeamParams.

No network calls. Every function here reads only from an already-loaded
fixture dict.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from ingest.errors import MissingProjectionError, RosterNotAvailableError
from ingest.models import (
    LeagueSchedule,
    LeagueSummary,
    PlayerProjection,
    RawTeam,
    SkippedPlayer,
)
from ingest.scoring import ScoringTable, build_scoring_table, score_stats
from ingest.slots import DEFAULT_POSITION_TO_SLOT, NON_STARTING_SLOTS
from sim.engine import PlayerParams, TeamParams

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

# ESPN's own stat category id for receptions. Used only to surface a
# human-readable "points per reception" figure in the league summary; the
# actual scoring computation never special-cases this id.
_RECEPTION_STAT_ID = "53"

# ESPN's projection-source and split-type ids for a full-season projection.
# statSourceId=1 is "projected" (0 is "actual"); statSplitTypeId=0 is a
# season total (not a single scoring period).
_PROJECTION_SOURCE_ID = 1
_SEASON_SPLIT_TYPE_ID = 0


def load_fixture(path: Path) -> dict[str, Any]:
    with path.open() as f:
        data: dict[str, Any] = json.load(f)
    return data


def parse_teams(raw: Mapping[str, Any]) -> tuple[RawTeam, ...]:
    """All team slots defined in the league, whether claimed or not.

    Ordered by ESPN team_id -- this order is authoritative for schedule
    array indices (see parse_schedule).
    """
    raw_teams = raw.get("teams")
    if not isinstance(raw_teams, list) or not raw_teams:
        raise ValueError("fixture has no 'teams' list")

    teams = [
        RawTeam(
            team_id=t["id"],
            name=t.get("name") or f"Team {t['id']}",
            abbrev=t.get("abbrev", ""),
            has_owner=bool(t.get("owners")),
        )
        for t in raw_teams
    ]
    teams.sort(key=lambda t: t.team_id)
    return tuple(teams)


def parse_schedule(
    raw: Mapping[str, Any], teams: tuple[RawTeam, ...], n_regular_weeks: int
) -> LeagueSchedule:
    """Build the (n_regular_weeks, n_teams) pairing array LeagueParams needs.

    Reads `matchupPeriodId` off each entry in the top-level `schedule` list
    -- deliberately not `scoringPeriodId`, which is a different field (an
    NFL week, not a fantasy matchup) and is not guaranteed equal to it.
    Playoff-round entries (matchupPeriodId beyond n_regular_weeks) are
    skipped; the engine plays its own sampled playoff rounds.
    """
    team_order = tuple(t.team_id for t in teams)
    index_of = {team_id: i for i, team_id in enumerate(team_order)}
    n_teams = len(team_order)

    pairings = np.full((n_regular_weeks, n_teams), -1, dtype=np.int64)

    raw_schedule = raw.get("schedule")
    if not isinstance(raw_schedule, list) or not raw_schedule:
        raise ValueError("fixture has no 'schedule' list")

    for matchup in raw_schedule:
        matchup_period_id = matchup.get("matchupPeriodId")
        if matchup_period_id is None or not (1 <= matchup_period_id <= n_regular_weeks):
            continue

        home, away = matchup.get("home"), matchup.get("away")
        if home is None or away is None:
            # A bye: one side of the matchup is absent. Not expected for an
            # even-sized league, but not this function's job to fabricate
            # an opponent -- leave the -1 sentinel so the completeness check
            # below raises with a precise location.
            continue

        home_id, away_id = home["teamId"], away["teamId"]
        if home_id not in index_of or away_id not in index_of:
            raise ValueError(
                f"schedule references team ids {home_id}/{away_id} not present in "
                "the parsed teams list"
            )

        week = matchup_period_id - 1
        h, a = index_of[home_id], index_of[away_id]
        pairings[week, h] = a
        pairings[week, a] = h

    missing_weeks, missing_teams = np.where(pairings == -1)
    if missing_weeks.size:
        w, t = int(missing_weeks[0]), int(missing_teams[0])
        raise ValueError(
            f"schedule is missing a pairing for matchupPeriodId {w + 1}, "
            f"team_id {team_order[t]}"
        )

    return LeagueSchedule(team_order=team_order, pairings=pairings)


def parse_scoring_table(raw: Mapping[str, Any]) -> ScoringTable:
    scoring_settings = raw.get("settings", {}).get("scoringSettings")
    if scoring_settings is None:
        raise ValueError("fixture has no settings.scoringSettings")
    return build_scoring_table(scoring_settings)


def _find_season_projection(
    player: Mapping[str, Any], season_id: int
) -> dict[str, Any] | None:
    for block in player.get("stats", []):
        if (
            block.get("statSourceId") == _PROJECTION_SOURCE_ID
            and block.get("statSplitTypeId") == _SEASON_SPLIT_TYPE_ID
            and block.get("seasonId") == season_id
        ):
            result: dict[str, Any] = block
            return result
    return None


def every_player_with_stats(raw: Mapping[str, Any]) -> dict[int, Any]:
    """Every player dict this fixture carries a `stats` block for -- the
    union of the free-agent pool (`_freeAgents`) and every team's drafted
    roster (`teams[].roster.entries[].playerPoolEntry.player`).

    Before a league drafts, `_freeAgents` alone is the entire relevant
    player universe (see docs/decisions.md Phase 1). Once it drafts, ESPN
    removes rostered players from `_freeAgents` entirely -- relying on that
    list alone silently loses every drafted player's projection, which is
    exactly what made `build_team_params` raise `MissingProjectionError` for
    every starter on a freshly-drafted league (see docs/decisions.md).
    Both sources carry an identical `player` dict shape (same keys, same
    `stats` structure) -- confirmed by direct inspection, not assumed -- so
    the rest of this module's per-player scoring/projection logic is
    unchanged; this only widens where player dicts are collected from.

    A player_id should appear in exactly one of the two sources (free agent
    XOR rostered) in a consistent fixture, so a plain last-write-wins merge
    is safe; there is nothing to reconcile between the two copies of the
    same player.
    """
    free_agents = raw.get("_freeAgents")
    if not isinstance(free_agents, list):
        raise ValueError("fixture has no '_freeAgents' list")

    raw_teams = raw.get("teams")
    if not isinstance(raw_teams, list):
        raise ValueError("fixture has no 'teams' list")

    by_id: dict[int, Any] = {}
    for entry in free_agents:
        player = entry["player"]
        by_id[player["id"]] = player
    for team in raw_teams:
        for roster_entry in team.get("roster", {}).get("entries", []):
            player = roster_entry["playerPoolEntry"]["player"]
            by_id[player["id"]] = player

    return by_id


def parse_player_pool(
    raw: Mapping[str, Any], scoring_table: ScoringTable
) -> tuple[tuple[PlayerProjection, ...], tuple[SkippedPlayer, ...]]:
    """Season-long projections for every player the fixture has stats for --
    free agents and drafted-roster players alike (see
    `every_player_with_stats`) -- scored under this league's own
    scoringSettings.

    Players with no usable projection are not silently dropped -- each is
    recorded as a SkippedPlayer with a specific reason, so a caller (or the
    demo) can say exactly who was excluded and why rather than presenting a
    quietly-incomplete pool.
    """
    players_by_id = every_player_with_stats(raw)

    season_id = raw["seasonId"]
    projections: list[PlayerProjection] = []
    skipped: list[SkippedPlayer] = []

    for player_id, player in players_by_id.items():
        name: str = player["fullName"]
        default_position_id: int = player["defaultPositionId"]

        block = _find_season_projection(player, season_id)
        if block is None:
            skipped.append(
                SkippedPlayer(
                    player_id,
                    name,
                    f"no season projection (statSourceId={_PROJECTION_SOURCE_ID}, "
                    f"statSplitTypeId={_SEASON_SPLIT_TYPE_ID}, seasonId={season_id}) "
                    "in fixture",
                )
            )
            continue

        applied_total = float(block.get("appliedTotal") or 0.0)
        applied_average = float(block.get("appliedAverage") or 0.0)
        if applied_total <= 0.0 or applied_average <= 0.0:
            skipped.append(
                SkippedPlayer(
                    player_id,
                    name,
                    "projection present but appliedTotal/appliedAverage is zero "
                    f"(appliedTotal={applied_total}, appliedAverage={applied_average})",
                )
            )
            continue

        lineup_slot_id = DEFAULT_POSITION_TO_SLOT.get(default_position_id)
        if lineup_slot_id is None:
            skipped.append(
                SkippedPlayer(
                    player_id,
                    name,
                    f"no known lineupSlotId mapping for defaultPositionId="
                    f"{default_position_id}",
                )
            )
            continue

        raw_stats = block.get("stats") or {}
        season_total = score_stats(raw_stats, scoring_table, lineup_slot_id)

        # Cross-check against ESPN's own appliedTotal for this exact stat
        # block. A divergence beyond float rounding means the scoring table
        # was built wrong -- fail loudly rather than ship a silently-wrong
        # number.
        tolerance = max(1.0, abs(applied_total) * 0.01)
        if abs(season_total - applied_total) > tolerance:
            raise ValueError(
                f"player {player_id} ({name}): computed season_total {season_total} "
                f"diverges from ESPN's appliedTotal {applied_total} by more than "
                "1% -- the scoring table is likely wrong"
            )

        games_projected = round(applied_total / applied_average)
        if games_projected <= 0:
            skipped.append(
                SkippedPlayer(
                    player_id,
                    name,
                    f"derived games_projected is non-positive ({games_projected})",
                )
            )
            continue

        ownership = player.get("ownership") or {}
        projections.append(
            PlayerProjection(
                player_id=player_id,
                name=name,
                default_position_id=default_position_id,
                eligible_slots=frozenset(player.get("eligibleSlots", [])),
                season_total=season_total,
                games_projected=games_projected,
                mean_points_per_game=season_total / games_projected,
                percent_owned=float(ownership.get("percentOwned") or 0.0),
                percent_started=float(ownership.get("percentStarted") or 0.0),
            )
        )

    return tuple(projections), tuple(skipped)


def parse_league_summary(raw: Mapping[str, Any]) -> LeagueSummary:
    """Parse everything Phase 1 can parse from a fixture into a LeagueSummary.

    Does not attempt to build sim.engine.TeamParams -- that requires a
    drafted roster (see build_team_params) which this fixture may not have.
    """
    schedule_settings = raw["settings"]["scheduleSettings"]
    n_regular_weeks = schedule_settings["matchupPeriodCount"]
    n_playoff_teams = schedule_settings["playoffTeamCount"]

    teams = parse_teams(raw)
    schedule = parse_schedule(raw, teams, n_regular_weeks)
    scoring_table = parse_scoring_table(raw)
    player_pool, skipped_players = parse_player_pool(raw, scoring_table)

    draft_detail = raw.get("draftDetail", {})
    status = raw.get("status", {})

    return LeagueSummary(
        league_id=raw["id"],
        name=raw["settings"]["name"],
        season_id=raw["seasonId"],
        size=raw["settings"]["size"],
        teams_joined=status.get("teamsJoined", sum(1 for t in teams if t.has_owner)),
        is_full=bool(status.get("isFull", False)),
        is_drafted=bool(draft_detail.get("drafted", False)),
        n_regular_weeks=n_regular_weeks,
        n_playoff_teams=n_playoff_teams,
        points_per_reception=scoring_table.rate.get(_RECEPTION_STAT_ID, 0.0),
        teams=teams,
        schedule=schedule,
        player_pool=player_pool,
        skipped_players=skipped_players,
    )


def build_team_params(
    raw: Mapping[str, Any], team_id: int, players_by_id: Mapping[int, PlayerParams]
) -> TeamParams:
    """Build a sim.engine.TeamParams for one team from its drafted roster.

    `players_by_id` must already be fully-formed PlayerParams (mean, sd,
    availability) -- this function only resolves roster entries to players
    and filters to starting slots, it does not derive scoring parameters.

    Raises RosterNotAvailableError if the team has no roster entries at all
    (e.g. the fixture predates the league's draft) rather than fabricating
    a lineup from the free-agent pool or ADP.
    """
    raw_teams = raw.get("teams")
    if not isinstance(raw_teams, list):
        raise ValueError("fixture has no 'teams' list")

    team = next((t for t in raw_teams if t["id"] == team_id), None)
    if team is None:
        raise ValueError(f"no team with id {team_id} in fixture")

    entries = team.get("roster", {}).get("entries", [])
    if not entries:
        drafted = raw.get("draftDetail", {}).get("drafted")
        raise RosterNotAvailableError(
            f"team {team_id} ({team.get('name', '?')}) has no roster entries -- "
            "refusing to fabricate a lineup. "
            f"fixture draftDetail.drafted={drafted!r}; if the draft hasn't "
            "happened yet, re-run scripts/fetch_fixture.py after it has."
        )

    starters: list[PlayerParams] = []
    for entry in entries:
        slot_id = entry["lineupSlotId"]
        if slot_id in NON_STARTING_SLOTS:
            continue
        player_id = entry["playerId"]
        params = players_by_id.get(player_id)
        if params is None:
            raise MissingProjectionError(
                f"team {team_id} roster includes player {player_id} in a starting "
                "slot with no usable projection -- refusing to invent one."
            )
        starters.append(params)

    if not starters:
        raise RosterNotAvailableError(
            f"team {team_id} ({team.get('name', '?')}) has roster entries but none "
            "in a starting lineup slot"
        )

    return TeamParams(team_id=team_id, name=team.get("name") or f"Team {team_id}", starters=tuple(starters))
