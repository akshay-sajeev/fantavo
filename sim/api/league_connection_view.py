"""Connecting a signed-in user's real ESPN league (Auth Phase B) -- see
docs/superpowers/specs/2026-08-14-auth-phase-b-league-connection-design.md.

No HTTP here (sim/api/app.py's 3 new routes), no direct ESPN calls
(ingest/espn_client.py owns those) -- this module owns exactly: validating
and persisting a connect attempt, saving which team is the user's, and
reporting connection state, each against the app_user columns
db/migrations/0004_league_connection.sql adds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from ingest.db import ingest_league
from ingest.espn_client import EspnFetchError, fetch_live_league
from sim.api.crypto import encrypt_credential


class LeagueConnectionError(ValueError):
    """The live ESPN fetch failed (wrong league id, bad/expired cookies,
    ESPN unreachable). Safe to show verbatim to the requesting user --
    there's no other account's existence to protect here, unlike
    auth_view's uniform login errors."""


class UnknownTeamError(ValueError):
    """team_id isn't one of the connected league's real teams."""


class NoConnectedLeagueError(ValueError):
    """The user has no espn_league_id yet -- set_team was called before
    connect_league."""


@dataclass(frozen=True)
class TeamOption:
    team_id: int
    name: str


@dataclass(frozen=True)
class ConnectionState:
    league_id: int | None
    season_id: int | None
    team_id: int | None
    connected_at: datetime | None


def resolve_current_season_id(now: datetime) -> int:
    """The ESPN fantasy season id is the season's start year (e.g. the 2026
    season runs Sept 2026 - Jan 2027 and is season_id=2026). Using the
    current calendar year is a deliberate simplification -- a user
    connecting in the Jan-Feb tail of the previous season would get the
    just-started, still-empty upcoming season instead. Historical-season
    selection is out of scope for this phase (see the design doc's Known
    Gaps)."""
    return now.year


def connect_league(
    conn: psycopg.Connection[Any],
    user_id: int,
    league_id: int,
    espn_s2: str | None,
    swid: str | None,
    now: datetime,
) -> tuple[TeamOption, ...]:
    """Validates by making one live ESPN fetch with the *submitted*
    credentials (nothing is persisted on failure), then on success:
    encrypts and saves the credentials plus league_id/season_id onto
    app_user, ingests the league via the existing, unchanged
    ingest_league(), and returns the team list read back from what was
    just ingested -- one live ESPN call total."""
    season_id = resolve_current_season_id(now)
    try:
        raw = fetch_live_league(league_id, season_id, espn_s2, swid)
    except EspnFetchError as exc:
        raise LeagueConnectionError(str(exc)) from exc

    summary = ingest_league(conn, raw, ingested_at=now)

    encrypted_s2 = encrypt_credential(espn_s2) if espn_s2 else None
    encrypted_swid = encrypt_credential(swid) if swid else None

    with conn.transaction():
        conn.execute(
            """
            UPDATE app_user
            SET espn_league_id = %s, espn_season_id = %s, espn_team_id = NULL,
                espn_s2_encrypted = %s, espn_swid_encrypted = %s,
                league_connected_at = %s
            WHERE user_id = %s
            """,
            (league_id, season_id, encrypted_s2, encrypted_swid, now, user_id),
        )

    return tuple(TeamOption(team_id=t.team_id, name=t.name) for t in summary.teams)


def set_team(conn: psycopg.Connection[Any], user_id: int, team_id: int) -> None:
    state = get_connection_state(conn, user_id)
    if state.league_id is None or state.season_id is None:
        raise NoConnectedLeagueError("no league connected yet")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM team WHERE league_id = %s AND season_id = %s AND team_id = %s",
            (state.league_id, state.season_id, team_id),
        )
        if cur.fetchone() is None:
            raise UnknownTeamError(f"team_id={team_id} is not a real team in this league")

    with conn.transaction():
        conn.execute(
            "UPDATE app_user SET espn_team_id = %s WHERE user_id = %s",
            (team_id, user_id),
        )


def get_connection_state(conn: psycopg.Connection[Any], user_id: int) -> ConnectionState:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT espn_league_id, espn_season_id, espn_team_id, league_connected_at
            FROM app_user WHERE user_id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()
    assert row is not None
    return ConnectionState(
        league_id=row[0], season_id=row[1], team_id=row[2], connected_at=row[3]
    )


def list_teams_for_league(
    conn: psycopg.Connection[Any], league_id: int, season_id: int
) -> tuple[TeamOption, ...]:
    """Reads the already-ingested team list straight from Postgres -- no
    ESPN call. Used by GET /leagues/me to re-render the team picker (e.g.
    after a page refresh) without needing the client to have kept the list
    connect_league originally returned."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT team_id, name FROM team WHERE league_id = %s AND season_id = %s ORDER BY team_id",
            (league_id, season_id),
        )
        rows = cur.fetchall()
    return tuple(TeamOption(team_id=r[0], name=r[1]) for r in rows)
