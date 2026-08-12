"""Tests for ingest.db against a real local Postgres instance.

Requires a reachable Postgres database (see docs/decisions.md Phase 3 for
setup: `brew install postgresql@16`, `brew services start postgresql@16`,
`createdb fantavo_test`). Connection info comes from the TEST_DATABASE_URL
env var, or defaults to ingest.db.DEFAULT_TEST_DSN
(postgresql:///fantavo_test -- a local Unix-socket connection with peer
auth, no password required).

No network calls to ESPN anywhere in this file -- the `conn` fixture only
ever talks to the local Postgres instance. CLAUDE.md's "fixtures, not live
calls" rule is about the ESPN API specifically; connecting to local infra is
explicitly fine (see the project owner's Phase 3 environment note).

If Postgres isn't reachable, tests here skip with a clear reason rather than
failing the whole suite -- consistent with this being local dev infra that a
given environment may not have set up, not a fixture every test in the repo
needs.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest

from ingest.db import (
    DATA_TABLES,
    DEFAULT_TEST_DSN,
    canonical_dump,
    ingest_league,
    run_migrations,
)
from ingest.parse import FIXTURES_DIR, load_fixture

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)
FIXTURE_PATH = FIXTURES_DIR / "league_raw_2026.json"

# A fixed instant, never datetime.now(). ingest_league's `ingested_at` must
# be identical across both calls in the idempotency test below for the two
# dumps to have any chance of matching -- see ingest/db.py's module
# docstring for why that argument is mandatory rather than a now() default.
FIXED_INGESTED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _try_connect() -> psycopg.Connection[Any] | None:
    try:
        return psycopg.connect(TEST_DSN, autocommit=False, connect_timeout=3)
    except psycopg.OperationalError:
        return None


@pytest.fixture(scope="module")
def raw() -> dict[str, Any]:
    return load_fixture(FIXTURE_PATH)


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection[Any]]:
    connection = _try_connect()
    if connection is None:
        pytest.skip(
            f"no reachable Postgres at {TEST_DSN!r} -- start it with "
            "`brew services start postgresql@16` and `createdb fantavo_test` "
            "(see docs/decisions.md Phase 3 for full setup)"
        )
    run_migrations(connection)
    connection.commit()
    # Clean slate before every test: this suite owns fantavo_test outright,
    # so truncate rather than depend on test ordering or prior runs.
    with connection.cursor() as cur:
        for table in DATA_TABLES:
            cur.execute(f"TRUNCATE TABLE {table} CASCADE")
    connection.commit()
    yield connection
    connection.close()


# --------------------------------------------------------------------------
# Migrations
# --------------------------------------------------------------------------


def test_run_migrations_is_idempotent(conn: psycopg.Connection[Any]) -> None:
    # The `conn` fixture already ran migrations once; running again must not
    # raise or double-apply anything.
    run_migrations(conn)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM schema_migrations")
        row = cur.fetchone()
        assert row is not None
        (count,) = row
    assert count >= 1


# --------------------------------------------------------------------------
# ingest_league: normalized data lands correctly
# --------------------------------------------------------------------------


def test_ingest_league_persists_normalized_data(
    conn: psycopg.Connection[Any], raw: dict[str, Any]
) -> None:
    summary = ingest_league(conn, raw, ingested_at=FIXED_INGESTED_AT)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM league")
        assert cur.fetchone() == (1,)

        cur.execute("SELECT COUNT(*) FROM team")
        assert cur.fetchone() == (len(summary.teams),) == (12,)

        cur.execute("SELECT COUNT(*) FROM player")
        assert cur.fetchone() == (len(summary.player_pool),) == (296,)

        cur.execute("SELECT COUNT(*) FROM matchup")
        assert cur.fetchone() == (84,)

        # This fixture is genuinely pre-draft (see docs/decisions.md Phase
        # 1) -- zero roster rows is the honest, correct result here, not a
        # sign the sync logic silently dropped anything.
        cur.execute("SELECT COUNT(*) FROM roster")
        assert cur.fetchone() == (0,)

        cur.execute(
            "SELECT points_per_reception FROM scoring_settings WHERE league_id = %s",
            (summary.league_id,),
        )
        # Confirmed full PPR from the league's own scoringSettings, not
        # assumed -- see docs/decisions.md Phase 1.
        assert cur.fetchone() == (1.0,)


# --------------------------------------------------------------------------
# Idempotency -- Phase 3's actual done criterion
# --------------------------------------------------------------------------


def test_ingesting_the_same_fixture_twice_is_byte_identical(
    conn: psycopg.Connection[Any], raw: dict[str, Any]
) -> None:
    """Phase 3's done criterion, verified against a real local Postgres
    instance: ingesting the same fixture twice produces byte-identical DB
    state."""
    ingest_league(conn, raw, ingested_at=FIXED_INGESTED_AT)
    conn.commit()
    first_dump = canonical_dump(conn)

    ingest_league(conn, raw, ingested_at=FIXED_INGESTED_AT)
    conn.commit()
    second_dump = canonical_dump(conn)

    assert first_dump == second_dump

    # Non-trivial: prove both dumps actually contain real ingested data,
    # not two empty states that happen to compare equal.
    assert len(first_dump) > 100_000
    assert b'"is_drafted": false' in first_dump
    assert b'"name": "Bijan Robinson"' in first_dump


def test_reingesting_with_a_different_ingested_at_changes_the_dump(
    conn: psycopg.Connection[Any], raw: dict[str, Any]
) -> None:
    """Contrast case for the test above: canonical_dump() is not vacuously
    equal for any two runs regardless of input -- a real re-ingest at a
    later wall-clock time (the one legitimate difference `ingested_at` is
    meant to capture) does change the dump."""
    ingest_league(conn, raw, ingested_at=FIXED_INGESTED_AT)
    conn.commit()
    first_dump = canonical_dump(conn)

    later = datetime(2026, 1, 2, tzinfo=timezone.utc)
    ingest_league(conn, raw, ingested_at=later)
    conn.commit()
    second_dump = canonical_dump(conn)

    assert first_dump != second_dump


def test_ingest_league_cleanly_removes_stale_child_rows(conn: psycopg.Connection[Any]) -> None:
    """'Overwrites cleanly' (CLAUDE.md) means a player who drops out of the
    pool between two ingests must not leave a stale row behind. Simulated
    here with a small synthetic two-player fixture shaped like the real one,
    re-ingested with the second player removed."""
    base: dict[str, Any] = {
        "id": 999,
        "seasonId": 2099,
        "scoringPeriodId": 1,
        "settings": {
            "name": "Stale Row Test League",
            "size": 2,
            "scheduleSettings": {"matchupPeriodCount": 1, "playoffTeamCount": 2},
            "scoringSettings": {
                "scoringItems": [{"statId": 53, "points": 1.0, "isReverseItem": False}]
            },
        },
        "status": {"teamsJoined": 2, "isFull": True},
        "draftDetail": {"drafted": False},
        "teams": [
            {"id": 1, "name": "A", "abbrev": "A", "owners": ["m1"], "roster": {"entries": []}},
            {"id": 2, "name": "B", "abbrev": "B", "owners": ["m2"], "roster": {"entries": []}},
        ],
        "schedule": [
            {
                "id": 1,
                "matchupPeriodId": 1,
                "winner": "UNDECIDED",
                "home": {"teamId": 1},
                "away": {"teamId": 2},
            }
        ],
    }

    def _player(pid: int, name: str) -> dict[str, Any]:
        return {
            "player": {
                "id": pid,
                "fullName": name,
                "defaultPositionId": 2,
                "eligibleSlots": [2, 20],
                "ownership": {"percentOwned": 10.0, "percentStarted": 5.0},
                "stats": [
                    {
                        "statSourceId": 1,
                        "statSplitTypeId": 0,
                        "seasonId": 2099,
                        "appliedTotal": 100.0,
                        "appliedAverage": 10.0,
                        "stats": {"53": 100.0},
                    }
                ],
            }
        }

    first_fixture = {**base, "_freeAgents": [_player(1, "Player One"), _player(2, "Player Two")]}
    ingest_league(conn, first_fixture, ingested_at=FIXED_INGESTED_AT)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM player WHERE league_id = 999")
        assert cur.fetchone() == (2,)

    second_fixture = {**base, "_freeAgents": [_player(1, "Player One")]}
    ingest_league(conn, second_fixture, ingested_at=FIXED_INGESTED_AT)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT player_id FROM player WHERE league_id = 999")
        assert [row[0] for row in cur.fetchall()] == [1]
