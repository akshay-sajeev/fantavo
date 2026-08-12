"""Load `sim.engine.LeagueParams` for an already-ingested league, straight
from Postgres.

Reuses the exact same parsing pipeline every other phase already
established (`ingest.parse` + `sim.params`) -- the only difference from a
fresh file-based run is where the raw ESPN payload comes from:
`league.raw_payload` in Postgres instead of a fixture file on disk. This is
precisely the "reprocessed without re-fetching" pattern CLAUDE.md's storage
convention promises, and it means the API never contains a second, parallel
parameter-derivation path -- if a result here ever diverges from
`ingest.parse` or `sim.params`, that is a bug in *this* module, never a
competing implementation of either.

No network calls: this module only ever talks to Postgres (via an
already-open connection) and to `ingest.parse` / `sim.params`, both of which
are themselves fixture-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from ingest.parse import (
    build_team_params,
    parse_player_pool,
    parse_schedule,
    parse_scoring_table,
    parse_teams,
)
from sim.engine import LeagueParams, PlayerParams
from sim.params.derive import derive_player_params_pool
from sim.params.variance import fit_position_cv


class LeagueNotIngestedError(Exception):
    """Raised when the requested (league_id, season_id) has no row in the
    `league` table. The API must not fabricate a league that was never
    ingested -- CLAUDE.md's "no invented numbers" rule applies to league
    existence, not just to individual stats."""


@dataclass(frozen=True)
class LoadedLeague:
    """Everything a route handler needs to call `simulate_seasons()` for one
    ingested league/season: the full `LeagueParams`, plus the same
    player_id -> PlayerParams mapping used to build it, so a caller (the
    what-if endpoint) can resolve roster-override player ids against real,
    already-derived params instead of inventing new ones.
    """

    league: LeagueParams
    players_by_id: dict[int, PlayerParams]


def load_raw_payload(
    conn: psycopg.Connection[Any], league_id: int, season_id: int
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT raw_payload FROM league WHERE league_id = %s AND season_id = %s",
            (league_id, season_id),
        )
        row = cur.fetchone()
    if row is None:
        raise LeagueNotIngestedError(
            f"no ingested league found for league_id={league_id}, season_id={season_id} "
            "-- ingest it first (ingest.db.ingest_league); refusing to fabricate params"
        )
    raw: dict[str, Any] = row[0]
    return raw


def resolve_season_id(
    conn: psycopg.Connection[Any], league_id: int, season_id: int | None
) -> int:
    """The plan's routes are `/league/{id}/...` with no season in the URL,
    but this schema's grain is (league_id, season_id) (Phase 3). If a caller
    doesn't pass an explicit `season_id` query/body field, resolve to the
    most recently ingested season for that league rather than guessing or
    silently picking an arbitrary row.
    """
    if season_id is not None:
        return season_id
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(season_id) FROM league WHERE league_id = %s", (league_id,))
        row = cur.fetchone()
    if row is None or row[0] is None:
        raise LeagueNotIngestedError(
            f"no ingested league found for league_id={league_id} in any season"
        )
    return int(row[0])


def load_league(conn: psycopg.Connection[Any], league_id: int, season_id: int) -> LoadedLeague:
    """Build a full `LeagueParams` for an ingested league, reading every
    team's real drafted roster.

    Propagates `ingest.errors.RosterNotAvailableError` /
    `MissingProjectionError` unchanged (raised inside
    `ingest.parse.build_team_params`) for any team with no drafted roster
    yet -- e.g. a genuinely pre-draft league -- rather than inventing a
    lineup. Also propagates `sim.params.errors.ParamsError` subclasses
    (missing/insufficient variance data) unchanged. Route handlers translate
    both into a clear HTTP error; this function never swallows them.
    """
    raw = load_raw_payload(conn, league_id, season_id)

    scoring_table = parse_scoring_table(raw)
    player_pool, _skipped = parse_player_pool(raw, scoring_table)
    position_cv = fit_position_cv(raw)
    players_by_id: dict[int, PlayerParams] = derive_player_params_pool(player_pool, position_cv)

    teams = parse_teams(raw)
    schedule_settings = raw["settings"]["scheduleSettings"]
    n_regular_weeks = schedule_settings["matchupPeriodCount"]
    n_playoff_teams = schedule_settings["playoffTeamCount"]
    schedule = parse_schedule(raw, teams, n_regular_weeks)

    team_params = tuple(build_team_params(raw, t.team_id, players_by_id) for t in teams)

    league = LeagueParams(
        teams=team_params,
        schedule=schedule.pairings,
        n_playoff_teams=n_playoff_teams,
    )
    return LoadedLeague(league=league, players_by_id=players_by_id)
