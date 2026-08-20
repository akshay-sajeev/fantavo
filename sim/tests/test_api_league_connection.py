"""Tests for sim.api.league_connection_view (Auth Phase B).

No real ESPN calls -- ingest.espn_client.fetch_live_league is monkeypatched
per-test to return sim/tests/conftest.py's raw_fixture, so these tests
exercise the real ingest_league() path end-to-end without a network call.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import DEFAULT_TEST_DSN
from ingest.espn_client import EspnAuthenticationError
from sim.api import app as app_module
from sim.api import auth_view, league_connection_view
from sim.api.cache import read_cached_simulation
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
TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)


@pytest.fixture()
def client(
    pg_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    monkeypatch.setattr(app_module, "start_scheduler", lambda dsn=None: None)
    with TestClient(app_module.app) as test_client:
        yield test_client


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


def test_connect_then_pick_team_over_http(
    client: TestClient, raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)

    signup_res = client.post(
        "/auth/signup", json={"email": "leaguehttp@example.com", "password": "a-real-password"}
    )
    headers = {"Authorization": f"Bearer {signup_res.json()['token']}"}

    me_before = client.get("/leagues/me", headers=headers)
    assert me_before.status_code == 200
    assert me_before.json()["league_id"] is None
    assert me_before.json()["teams"] == []

    connect_res = client.post(
        "/leagues/connect", json={"league_id": raw_fixture["id"]}, headers=headers
    )
    assert connect_res.status_code == 200
    teams = connect_res.json()["teams"]
    assert len(teams) == len(raw_fixture["teams"])

    me_after_connect = client.get("/leagues/me", headers=headers)
    assert me_after_connect.json()["league_id"] == raw_fixture["id"]
    assert me_after_connect.json()["team_id"] is None
    assert len(me_after_connect.json()["teams"]) == len(raw_fixture["teams"])

    team_res = client.post(
        "/leagues/team", json={"team_id": teams[0]["team_id"]}, headers=headers
    )
    assert team_res.status_code == 204

    me_final = client.get("/leagues/me", headers=headers)
    assert me_final.json()["team_id"] == teams[0]["team_id"]
    assert me_final.json()["teams"] == []


def test_leagues_connect_requires_auth(client: TestClient) -> None:
    res = client.post("/leagues/connect", json={"league_id": 12345})
    assert res.status_code == 401


def test_leagues_me_requires_auth(client: TestClient) -> None:
    assert client.get("/leagues/me").status_code == 401


def test_leagues_connect_returns_400_with_a_specific_message_on_espn_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The failure path is where a submitted credential is most likely to
    leak (into an error message or a log line), so this submits a
    distinctive fake espn_s2 marker and proves that exact string appears
    nowhere in the response body or the captured log output -- CLAUDE.md's
    "never include them in error messages" rule, asserted end-to-end rather
    than assumed. The marker is invented for this test; it is not a real
    credential."""
    fake_espn_s2 ="a-very-specific-fake-cookie-marker-xyz"

    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise EspnAuthenticationError("bad cookies")

    monkeypatch.setattr(league_connection_view, "fetch_live_league", _raise)

    signup_res = client.post(
        "/auth/signup", json={"email": "leaguefail@example.com", "password": "a-real-password"}
    )
    headers = {"Authorization": f"Bearer {signup_res.json()['token']}"}

    with caplog.at_level(logging.DEBUG):
        res = client.post(
            "/leagues/connect",
            json={"league_id": 12345, "espn_s2": fake_espn_s2},
            headers=headers,
        )

    assert res.status_code == 400
    # Never echoes the submitted credential -- anywhere in the body, not
    # just in `detail`.
    assert fake_espn_s2 not in json.dumps(res.json())
    assert fake_espn_s2 not in caplog.text


def test_leagues_team_rejects_a_fake_team_over_http(
    client: TestClient, raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)

    signup_res = client.post(
        "/auth/signup", json={"email": "leaguefaketeam@example.com", "password": "a-real-password"}
    )
    headers = {"Authorization": f"Bearer {signup_res.json()['token']}"}
    client.post("/leagues/connect", json={"league_id": raw_fixture["id"]}, headers=headers)

    res = client.post("/leagues/team", json={"team_id": 999999}, headers=headers)
    assert res.status_code == 400


def test_connect_league_precomputes_odds_immediately(
    client: TestClient,
    raw_fixture: dict[str, Any],
    pg_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for the dashboard-404 gap: a newly-connected league
    must have a cached simulation the instant connect finishes, not just
    after the next daily Cron run."""
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    signup_res = client.post(
        "/auth/signup",
        json={"email": "precompute-on-connect@example.com", "password": "a-real-password"},
    )
    headers = {"Authorization": f"Bearer {signup_res.json()['token']}"}

    connect_res = client.post(
        "/leagues/connect", json={"league_id": raw_fixture["id"]}, headers=headers
    )
    assert connect_res.status_code == 200

    cached = read_cached_simulation(pg_conn, raw_fixture["id"], raw_fixture["seasonId"])
    assert cached is not None
    assert cached.n_sims > 0


def test_connect_league_still_succeeds_when_precompute_cannot_run_yet(
    client: TestClient,
    raw_fixture: dict[str, Any],
    pg_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A not-yet-drafted league: connect_league's own ingest succeeds fine
    (empty rosters store without error), only the post-connect precompute
    can't run -- must not fail the connect response."""
    pre_draft_raw = copy.deepcopy(raw_fixture)
    pre_draft_raw["id"] = raw_fixture["id"] + 1  # distinct from the real league's id
    pre_draft_raw["draftDetail"]["drafted"] = False
    for team in pre_draft_raw["teams"]:
        team["roster"]["entries"] = []
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: pre_draft_raw)

    signup_res = client.post(
        "/auth/signup",
        json={"email": "precompute-skip-on-connect@example.com", "password": "a-real-password"},
    )
    headers = {"Authorization": f"Bearer {signup_res.json()['token']}"}

    connect_res = client.post(
        "/leagues/connect", json={"league_id": pre_draft_raw["id"]}, headers=headers
    )
    assert connect_res.status_code == 200
    assert len(connect_res.json()["teams"]) == len(pre_draft_raw["teams"])

    cached = read_cached_simulation(pg_conn, pre_draft_raw["id"], pre_draft_raw["seasonId"])
    assert cached is None
