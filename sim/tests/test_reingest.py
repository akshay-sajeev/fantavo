"""Tests for sim.api.reingest -- the recurring background job that keeps
every connected user's league current. Mirrors sim/tests/test_api_league_
connection.py's approach: fetch_live_league is monkeypatched, never called
for real."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest

from ingest.db import ingest_league
from ingest.errors import IngestError
from ingest.espn_client import EspnFetchError
from sim.api import auth_view, league_connection_view, reingest
from sim.api.reingest import reingest_all_connected_users, reingest_user

FIXED_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _make_connected_user(
    pg_conn: psycopg.Connection[Any], email: str, raw_fixture: dict[str, Any]
) -> int:
    user = auth_view.create_user(pg_conn, email, "a-real-password", FIXED_NOW)
    league_connection_view.connect_league(
        pg_conn, user.user_id, raw_fixture["id"], None, None, FIXED_NOW
    )
    return user.user_id


def test_reingest_user_re_ingests_the_connected_league(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    user_id = _make_connected_user(pg_conn, "reingest1@example.com", raw_fixture)

    # Deliberately corrupt the stored season to a stale value, so the
    # assertion below proves espn_season_id is actively re-derived from what
    # ingest_league really parsed rather than trusted as-stored (Item 3 of
    # the final-review fix wave: a season rollover must not leave a
    # connected user re-fetching last season forever).
    pg_conn.execute("UPDATE app_user SET espn_season_id = 1999 WHERE user_id = %s", (user_id,))

    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: raw_fixture)
    later = datetime(2026, 6, 2, tzinfo=timezone.utc)
    reingest_user(pg_conn, user_id, later)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ingested_at FROM league WHERE league_id = %s AND season_id = %s",
            (raw_fixture["id"], 2026),
        )
        row = cur.fetchone()
    assert row is not None
    (ingested_at,) = row
    assert ingested_at == later

    with pg_conn.cursor() as cur:
        cur.execute("SELECT espn_season_id FROM app_user WHERE user_id = %s", (user_id,))
        season_row = cur.fetchone()
    assert season_row is not None
    assert season_row[0] == raw_fixture["seasonId"]


def test_reingest_all_connected_users_skips_a_failing_user_and_continues(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    failing_user_id = _make_connected_user(pg_conn, "failing@example.com", raw_fixture)
    ok_user_id = _make_connected_user(pg_conn, "ok@example.com", raw_fixture)
    pg_conn.commit()

    call_count = {"n": 0}

    def _flaky_fetch(*args: Any, **kwargs: Any) -> Any:
        call_count["n"] += 1
        # Both users share the same league_id in this test, so distinguish
        # by call order isn't reliable -- instead fail on the first call
        # only, succeed on the rest, to prove one failure doesn't abort
        # the batch regardless of which user_id it belongs to.
        if call_count["n"] == 1:
            raise EspnFetchError("simulated ESPN outage")
        return raw_fixture

    monkeypatch.setattr(reingest, "fetch_live_league", _flaky_fetch)

    reingest_all_connected_users(pg_conn, datetime(2026, 6, 2, tzinfo=timezone.utc))

    # No assertion on which specific user's fetch failed (both share a
    # league_id, so ingested_at ends up identical either way) -- the real
    # claim is that reingest_all_connected_users itself did not raise and
    # processed both users despite one EspnFetchError.
    assert call_count["n"] == 2
    assert failing_user_id != ok_user_id  # sanity: two distinct users were created


def test_reingest_all_connected_users_skips_a_user_whose_ingest_fails(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """IngestError (e.g. a malformed ESPN payload) is isolated per user just
    like EspnFetchError -- one bad payload must not abort the whole batch.
    Same structure as the EspnFetchError test above, one layer deeper."""
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    failing_user_id = _make_connected_user(pg_conn, "badpayload@example.com", raw_fixture)
    ok_user_id = _make_connected_user(pg_conn, "goodpayload@example.com", raw_fixture)
    pg_conn.commit()

    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: raw_fixture)

    call_count = {"n": 0}

    def _flaky_ingest(*args: Any, **kwargs: Any) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise IngestError("simulated malformed ESPN payload")
        return ingest_league(*args, **kwargs)

    monkeypatch.setattr(reingest, "ingest_league", _flaky_ingest)

    reingest_all_connected_users(pg_conn, datetime(2026, 6, 2, tzinfo=timezone.utc))

    assert call_count["n"] == 2
    assert failing_user_id != ok_user_id  # sanity: two distinct users were created
