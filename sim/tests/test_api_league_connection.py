"""Tests for sim.api.league_connection_view (Auth Phase B).

No real ESPN calls -- ingest.espn_client.fetch_live_league is monkeypatched
per-test to return sim/tests/conftest.py's raw_fixture, so these tests
exercise the real ingest_league() path end-to-end without a network call.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest

from ingest.espn_client import EspnAuthenticationError
from sim.api import auth_view, league_connection_view
from sim.api.league_connection_view import (
    LeagueConnectionError,
    NoConnectedLeagueError,
    UnknownTeamError,
    connect_league,
    get_connection_state,
    list_teams_for_league,
    resolve_current_season_id,
    set_team,
)

FIXED_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _make_user(pg_conn: psycopg.Connection[Any]) -> int:
    user = auth_view.create_user(pg_conn, "connector@example.com", "a-real-password", FIXED_NOW)
    return user.user_id


def test_resolve_current_season_id_uses_the_calendar_year() -> None:
    assert resolve_current_season_id(FIXED_NOW) == 2026


def test_connect_league_ingests_and_returns_teams(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    user_id = _make_user(pg_conn)

    teams = connect_league(pg_conn, user_id, raw_fixture["id"], None, None, FIXED_NOW)

    assert {t.team_id for t in teams} == {t["id"] for t in raw_fixture["teams"]}

    state = get_connection_state(pg_conn, user_id)
    assert state.league_id == raw_fixture["id"]
    assert state.season_id == 2026
    assert state.team_id is None
    assert state.connected_at == FIXED_NOW


def test_connect_league_encrypts_credentials_never_stores_plaintext(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    user_id = _make_user(pg_conn)
    espn_s2 = "a-very-specific-fake-cookie-value"
    swid = "{FAKE-SWID-0000-0000-000000000000}"

    connect_league(pg_conn, user_id, raw_fixture["id"], espn_s2, swid, FIXED_NOW)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT espn_s2_encrypted, espn_swid_encrypted FROM app_user WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    assert row is not None
    encrypted_s2, encrypted_swid = row
    assert espn_s2.encode("utf-8") not in encrypted_s2
    assert swid.encode("utf-8") not in encrypted_swid


def test_connect_league_public_league_stores_no_credentials(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    user_id = _make_user(pg_conn)

    connect_league(pg_conn, user_id, raw_fixture["id"], None, None, FIXED_NOW)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT espn_s2_encrypted, espn_swid_encrypted FROM app_user WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    assert row is not None
    encrypted_s2, encrypted_swid = row
    assert encrypted_s2 is None
    assert encrypted_swid is None


def test_connect_league_persists_nothing_on_espn_failure(
    pg_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise EspnAuthenticationError("bad cookies")

    monkeypatch.setattr(league_connection_view, "fetch_live_league", _raise)
    user_id = _make_user(pg_conn)

    with pytest.raises(LeagueConnectionError):
        connect_league(pg_conn, user_id, 12345, "wrong", "wrong", FIXED_NOW)

    state = get_connection_state(pg_conn, user_id)
    assert state.league_id is None


def test_set_team_accepts_a_real_team_and_rejects_a_fake_one(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    user_id = _make_user(pg_conn)
    connect_league(pg_conn, user_id, raw_fixture["id"], None, None, FIXED_NOW)
    real_team_id = raw_fixture["teams"][0]["id"]

    set_team(pg_conn, user_id, real_team_id)
    assert get_connection_state(pg_conn, user_id).team_id == real_team_id

    with pytest.raises(UnknownTeamError):
        set_team(pg_conn, user_id, 999999)


def test_set_team_without_a_connected_league_raises(pg_conn: psycopg.Connection[Any]) -> None:
    user_id = _make_user(pg_conn)
    with pytest.raises(NoConnectedLeagueError):
        set_team(pg_conn, user_id, 1)


def test_list_teams_for_league_reads_from_the_team_table(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    user_id = _make_user(pg_conn)
    connect_league(pg_conn, user_id, raw_fixture["id"], None, None, FIXED_NOW)

    teams = list_teams_for_league(pg_conn, raw_fixture["id"], 2026)
    assert {t.team_id for t in teams} == {t["id"] for t in raw_fixture["teams"]}
