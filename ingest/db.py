"""Postgres persistence for ingested ESPN league data.

Applies the SQL migrations in db/migrations/ (see that directory for the
schema) and performs an idempotent full-sync of one league/season's parsed
fixture data into Postgres:

- `league` and `scoring_settings` are single rows per (league_id, season_id)
  and are upserted in place (INSERT ... ON CONFLICT DO UPDATE).
- `team`, `player`, `roster` and `matchup` are child tables resynced by
  delete-then-insert, scoped to (league_id, season_id): every row for that
  league/season is removed and reinserted from the current fixture in one
  transaction. This is what CLAUDE.md's "ingest is idempotent: re-running
  for the same week overwrites cleanly" means in practice -- a player who
  drops out of the pool, or a roster entry that changes, does not leave a
  stale row behind.

No network calls: this module only ever talks to Postgres, never to ESPN.
Every function that touches the database takes an already-open
`psycopg.Connection` -- this module does not read `.env` or manage
connection pooling, both of which belong to a caller (a script, a future API
service, or a test fixture).

`ingested_at` is a required, explicit argument, never a server-side
`now()` default. CLAUDE.md requires every stochastic function to take an
explicit `rng` rather than reach for implicit global state; the same
discipline applies here to wall-clock time, for the same reason -- an
implicit `now()` would make two ingests of the identical fixture produce
different `ingested_at` values and never compare byte-identical, which is
exactly what ingest/tests/test_db.py needs to assert. Callers doing a real
ingest pass `datetime.now(timezone.utc)` once, themselves.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg

from ingest.models import LeagueSummary, PlayerProjection, RawTeam
from ingest.parse import parse_league_summary

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"

# Local Homebrew Postgres, connected over the default Unix socket with peer
# auth (no password) -- see docs/decisions.md Phase 3 for how this was set
# up. Not a secret, so (unlike ESPN_S2/SWID) it is fine to default in code
# rather than requiring .env.
DEFAULT_DEV_DSN = "postgresql:///fantavo_dev"
DEFAULT_TEST_DSN = "postgresql:///fantavo_test"

# Every table an ingest run writes to, in a stable order used by
# canonical_dump. Order doesn't affect correctness (each SELECT is
# independently sorted by primary key) but keeps dumps easy to read.
DATA_TABLES: tuple[str, ...] = (
    "league",
    "scoring_settings",
    "team",
    "player",
    "roster",
    "matchup",
)


def connect(dsn: str) -> psycopg.Connection[Any]:
    """Open a connection with autocommit off, so callers control transactions."""
    return psycopg.connect(dsn, autocommit=False)


def run_migrations(conn: psycopg.Connection[Any], migrations_dir: Path = MIGRATIONS_DIR) -> None:
    """Apply any not-yet-applied *.sql file in `migrations_dir`, in filename
    order, tracked in a `schema_migrations` table. Safe to call every time a
    caller connects -- already-applied migrations are skipped."""
    with conn.transaction():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename    TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            row[0] for row in conn.execute("SELECT filename FROM schema_migrations").fetchall()
        }
        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in applied:
                continue
            conn.execute(path.read_text())
            conn.execute(
                "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
            )


def _upsert_league(
    conn: psycopg.Connection[Any],
    summary: LeagueSummary,
    raw: Mapping[str, Any],
    ingested_at: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO league (
            league_id, season_id, name, size, teams_joined, is_full,
            is_drafted, n_regular_weeks, n_playoff_teams,
            points_per_reception, raw_payload, ingested_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (league_id, season_id) DO UPDATE SET
            name = EXCLUDED.name,
            size = EXCLUDED.size,
            teams_joined = EXCLUDED.teams_joined,
            is_full = EXCLUDED.is_full,
            is_drafted = EXCLUDED.is_drafted,
            n_regular_weeks = EXCLUDED.n_regular_weeks,
            n_playoff_teams = EXCLUDED.n_playoff_teams,
            points_per_reception = EXCLUDED.points_per_reception,
            raw_payload = EXCLUDED.raw_payload,
            ingested_at = EXCLUDED.ingested_at
        """,
        (
            summary.league_id,
            summary.season_id,
            summary.name,
            summary.size,
            summary.teams_joined,
            summary.is_full,
            summary.is_drafted,
            summary.n_regular_weeks,
            summary.n_playoff_teams,
            summary.points_per_reception,
            json.dumps(raw),
            ingested_at,
        ),
    )


def _upsert_scoring_settings(
    conn: psycopg.Connection[Any],
    league_id: int,
    season_id: int,
    points_per_reception: float,
    raw_scoring_settings: Mapping[str, Any],
    ingested_at: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO scoring_settings (
            league_id, season_id, points_per_reception, raw_scoring_settings, ingested_at
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (league_id, season_id) DO UPDATE SET
            points_per_reception = EXCLUDED.points_per_reception,
            raw_scoring_settings = EXCLUDED.raw_scoring_settings,
            ingested_at = EXCLUDED.ingested_at
        """,
        (
            league_id,
            season_id,
            points_per_reception,
            json.dumps(raw_scoring_settings),
            ingested_at,
        ),
    )


def _sync_teams(
    conn: psycopg.Connection[Any],
    league_id: int,
    season_id: int,
    teams: tuple[RawTeam, ...],
    raw_teams_by_id: Mapping[int, Mapping[str, Any]],
    ingested_at: datetime,
) -> None:
    conn.execute(
        "DELETE FROM team WHERE league_id = %s AND season_id = %s", (league_id, season_id)
    )
    rows = [
        (
            league_id,
            season_id,
            t.team_id,
            t.name,
            t.abbrev,
            t.has_owner,
            json.dumps(raw_teams_by_id[t.team_id]),
            ingested_at,
        )
        for t in teams
    ]
    if rows:
        conn.cursor().executemany(
            """
            INSERT INTO team (
                league_id, season_id, team_id, name, abbrev, has_owner, raw_team, ingested_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )


def _sync_players(
    conn: psycopg.Connection[Any],
    league_id: int,
    season_id: int,
    player_pool: tuple[PlayerProjection, ...],
    raw_players_by_id: Mapping[int, Mapping[str, Any]],
    ingested_at: datetime,
) -> None:
    conn.execute(
        "DELETE FROM player WHERE league_id = %s AND season_id = %s", (league_id, season_id)
    )
    rows = [
        (
            league_id,
            season_id,
            p.player_id,
            p.name,
            p.default_position_id,
            sorted(p.eligible_slots),
            p.season_total,
            p.games_projected,
            p.mean_points_per_game,
            p.percent_owned,
            p.percent_started,
            json.dumps(raw_players_by_id[p.player_id]),
            ingested_at,
        )
        for p in player_pool
    ]
    if rows:
        conn.cursor().executemany(
            """
            INSERT INTO player (
                league_id, season_id, player_id, name, default_position_id,
                eligible_slots, season_total, games_projected,
                mean_points_per_game, percent_owned, percent_started,
                raw_player, ingested_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )


def _sync_rosters(
    conn: psycopg.Connection[Any],
    league_id: int,
    season_id: int,
    raw_teams: Sequence[Mapping[str, Any]],
    ingested_at: datetime,
) -> None:
    """Every roster entry for every team, bench and IR included.

    Stores whatever the fixture actually has -- for a pre-draft fixture like
    fixtures/league_raw_2026.json that is zero rows for every team (see
    docs/decisions.md Phase 1), which this function must not treat as an
    error: an empty roster is the true state of a pre-draft league, not a
    parsing failure.
    """
    conn.execute(
        "DELETE FROM roster WHERE league_id = %s AND season_id = %s", (league_id, season_id)
    )
    rows = []
    for team in raw_teams:
        team_id = team["id"]
        for entry in team.get("roster", {}).get("entries", []):
            rows.append(
                (
                    league_id,
                    season_id,
                    team_id,
                    entry["playerId"],
                    entry["lineupSlotId"],
                    json.dumps(entry),
                    ingested_at,
                )
            )
    if rows:
        conn.cursor().executemany(
            """
            INSERT INTO roster (
                league_id, season_id, team_id, player_id, lineup_slot_id, raw_entry, ingested_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )


def _sync_matchups(
    conn: psycopg.Connection[Any],
    league_id: int,
    season_id: int,
    raw_schedule: Sequence[Mapping[str, Any]],
    ingested_at: datetime,
) -> None:
    """Every raw schedule entry, keyed by ESPN's own matchup id.

    Deliberately reads `matchupPeriodId`, never `scoringPeriodId` -- see
    CLAUDE.md's domain glossary and ingest.parse.parse_schedule, which draws
    the same distinction. Unlike parse_schedule (which only needs the
    regular-season pairing array for the engine), this stores every schedule
    entry the fixture has, playoff rounds included, since persistence's job
    is to keep the source data, not to filter it down to what one particular
    consumer needs.
    """
    conn.execute(
        "DELETE FROM matchup WHERE league_id = %s AND season_id = %s", (league_id, season_id)
    )
    rows = []
    for entry in raw_schedule:
        home = entry.get("home")
        away = entry.get("away")
        rows.append(
            (
                league_id,
                season_id,
                entry["id"],
                entry["matchupPeriodId"],
                home["teamId"] if home else None,
                away["teamId"] if away else None,
                entry.get("winner"),
                json.dumps(entry),
                ingested_at,
            )
        )
    if rows:
        conn.cursor().executemany(
            """
            INSERT INTO matchup (
                league_id, season_id, espn_matchup_id, matchup_period_id,
                home_team_id, away_team_id, winner, raw_matchup, ingested_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )


def ingest_league(
    conn: psycopg.Connection[Any], raw: Mapping[str, Any], ingested_at: datetime
) -> LeagueSummary:
    """Parse `raw` (an already-loaded fixture dict) and persist it to
    Postgres in one transaction. Returns the parsed LeagueSummary so callers
    (e.g. a CLI) can report on what was written without re-parsing.

    Idempotent: calling this twice with the same `raw` and `ingested_at`
    leaves the database in the same state both times (see module docstring
    and ingest/tests/test_db.py).
    """
    summary = parse_league_summary(raw)
    league_id, season_id = summary.league_id, summary.season_id

    raw_teams_list = raw["teams"]
    raw_teams_by_id = {t["id"]: t for t in raw_teams_list}
    raw_scoring_settings = raw["settings"]["scoringSettings"]
    raw_players_by_id = {fa["player"]["id"]: fa["player"] for fa in raw["_freeAgents"]}

    with conn.transaction():
        _upsert_league(conn, summary, raw, ingested_at)
        _upsert_scoring_settings(
            conn, league_id, season_id, summary.points_per_reception, raw_scoring_settings,
            ingested_at,
        )
        _sync_teams(conn, league_id, season_id, summary.teams, raw_teams_by_id, ingested_at)
        _sync_players(
            conn, league_id, season_id, summary.player_pool, raw_players_by_id, ingested_at
        )
        _sync_rosters(conn, league_id, season_id, raw_teams_list, ingested_at)
        _sync_matchups(conn, league_id, season_id, raw["schedule"], ingested_at)

    return summary


def _serialize_row(columns: Sequence[str], row: Sequence[Any]) -> str:
    """One row as a canonical, deterministically-ordered JSON line.

    `sort_keys=True` guards against Postgres's jsonb storage not preserving
    the original key order of a stored JSON object (jsonb reorders keys
    internally) -- without it, two runs that write byte-identical *content*
    could still round-trip to differently-ordered dict keys purely as an
    artifact of Postgres internals, which would be a false idempotency
    failure, not a real one.
    """
    record = dict(zip(columns, row, strict=True))
    return json.dumps(record, sort_keys=True, default=str)


def canonical_dump(
    conn: psycopg.Connection[Any], tables: Sequence[str] = DATA_TABLES
) -> bytes:
    """A deterministic byte serialization of every row in `tables`, ordered
    by each table's own primary key.

    Used by ingest/tests/test_db.py to assert that ingesting the same
    fixture twice produces byte-identical database state: call this after
    each of two ingest_league() runs and compare the two return values with
    plain `==`.
    """
    lines: list[str] = []
    for table in tables:
        with conn.cursor() as cur:
            # `table` always comes from DATA_TABLES (a hardcoded constant) or a
            # caller-supplied whitelist -- never user input -- so this is not
            # a SQL-injection-prone f-string despite the shape.
            cur.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3, 4, 5")
            columns = [desc.name for desc in cur.description or []]
            lines.append(f"-- {table}")
            for row in cur.fetchall():
                lines.append(_serialize_row(columns, row))
    return "\n".join(lines).encode("utf-8")


def dsn_from_env(var: str, default: str) -> str:
    return os.environ.get(var, default)


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "fixture", nargs="?", default=None, help="Path to a fixture JSON file"
    )
    parser.add_argument(
        "--dsn", default=None, help=f"Postgres DSN (default: {DEFAULT_DEV_DSN})"
    )
    args = parser.parse_args()

    from datetime import timezone

    from ingest.parse import FIXTURES_DIR, load_fixture

    dsn = args.dsn or dsn_from_env("DATABASE_URL", DEFAULT_DEV_DSN)
    fixture_path = Path(args.fixture) if args.fixture else FIXTURES_DIR / "league_raw_2026.json"

    with connect(dsn) as conn:
        run_migrations(conn)
        raw = load_fixture(fixture_path)
        summary = ingest_league(conn, raw, ingested_at=datetime.now(timezone.utc))
        conn.commit()
        print(
            f"Ingested league {summary.league_id} season {summary.season_id}: "
            f"{len(summary.teams)} teams, {len(summary.player_pool)} players, "
            f"into {dsn}"
        )


if __name__ == "__main__":
    _main()
