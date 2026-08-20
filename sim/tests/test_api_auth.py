"""Tests for sim.api.auth_view and the /auth/* routes.

Split the same way every other sim.api test module is: fast unit tests
directly against auth_view's functions where no DB is needed, then
integration tests through the FastAPI TestClient for the full HTTP surface.
No network calls to ESPN anywhere -- only to the local Postgres instance
(see sim/tests/conftest.py's pg_conn fixture for the skip-if-unreachable
pattern this reuses).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import DEFAULT_TEST_DSN
from ingest.espn_client import EspnFetchError
from sim.api import app as app_module
from sim.api import league_connection_view, reingest
from sim.api.auth_view import (
    SESSION_LIFETIME_DAYS,
    THROTTLE_LOCKOUT_MINUTES,
    THROTTLE_MAX_FAILURES,
    THROTTLE_WINDOW_MINUTES,
    AccountLockedError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidSessionError,
    authenticate_user,
    create_session,
    create_user,
    delete_session,
    hash_password,
    normalize_email,
    validate_session,
    verify_password,
)

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)


@pytest.fixture()
def client(
    pg_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    monkeypatch.setattr(app_module, "start_scheduler", lambda dsn=None: None)
    with TestClient(app_module.app) as test_client:
        yield test_client


def test_normalize_email_lowercases_and_trims() -> None:
    assert normalize_email("  Foo@Bar.com  ") == "foo@bar.com"


def test_normalize_email_is_idempotent() -> None:
    once = normalize_email("Foo@Bar.com")
    assert normalize_email(once) == once


def test_hash_password_roundtrips_through_verify() -> None:
    password = "correct horse battery staple"
    password_hash = hash_password(password)
    assert verify_password(password, password_hash) is True


def test_verify_password_rejects_wrong_password() -> None:
    password_hash = hash_password("correct horse battery staple")
    assert verify_password("wrong password entirely", password_hash) is False


def test_hash_password_never_returns_the_plaintext() -> None:
    password = "correct horse battery staple"
    password_hash = hash_password(password)
    assert password not in password_hash


FIXED_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_create_user_returns_the_new_user(pg_conn: psycopg.Connection[Any]) -> None:
    user = create_user(pg_conn, "New@Example.com", "a-real-password", FIXED_NOW)
    assert user.email == "New@Example.com"
    assert user.user_id > 0


def test_create_user_rejects_duplicate_email_case_insensitively(
    pg_conn: psycopg.Connection[Any],
) -> None:
    create_user(pg_conn, "dupe@example.com", "a-real-password", FIXED_NOW)
    with pytest.raises(EmailAlreadyRegisteredError):
        create_user(pg_conn, "DUPE@example.com", "a-different-password", FIXED_NOW)


def test_create_user_rejects_short_password(pg_conn: psycopg.Connection[Any]) -> None:
    with pytest.raises(ValueError, match="at least"):
        create_user(pg_conn, "short@example.com", "short1", FIXED_NOW)


def test_create_user_stores_only_a_hash_never_the_plaintext_password(
    pg_conn: psycopg.Connection[Any],
) -> None:
    password = "a-real-password-nobody-guesses"
    create_user(pg_conn, "hashcheck@example.com", password, FIXED_NOW)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT password_hash FROM app_user WHERE email_norm = %s", ("hashcheck@example.com",))
        row = cur.fetchone()
    assert row is not None
    assert password not in row[0]


def test_create_session_then_validate_returns_the_same_user(
    pg_conn: psycopg.Connection[Any],
) -> None:
    user = create_user(pg_conn, "sessions@example.com", "a-real-password", FIXED_NOW)
    token = create_session(pg_conn, user, FIXED_NOW)
    validated = validate_session(pg_conn, token, FIXED_NOW)
    assert validated == user


def test_validate_session_rejects_garbage_token(pg_conn: psycopg.Connection[Any]) -> None:
    with pytest.raises(InvalidSessionError):
        validate_session(pg_conn, "not-a-real-token", FIXED_NOW)


def test_validate_session_rejects_expired_session(pg_conn: psycopg.Connection[Any]) -> None:
    user = create_user(pg_conn, "expiry@example.com", "a-real-password", FIXED_NOW)
    token = create_session(pg_conn, user, FIXED_NOW)
    long_after_expiry = FIXED_NOW + timedelta(days=31)
    with pytest.raises(InvalidSessionError):
        validate_session(pg_conn, token, long_after_expiry)


def test_validate_session_extends_expires_at(pg_conn: psycopg.Connection[Any]) -> None:
    user = create_user(pg_conn, "sliding@example.com", "a-real-password", FIXED_NOW)
    token = create_session(pg_conn, user, FIXED_NOW)
    later = FIXED_NOW + timedelta(days=29)
    validate_session(pg_conn, token, later)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT expires_at, last_seen_at FROM user_session WHERE user_id = %s",
            (user.user_id,),
        )
        row = cur.fetchone()
    assert row is not None
    expires_at, last_seen_at = row
    assert expires_at == later + timedelta(days=SESSION_LIFETIME_DAYS)
    assert last_seen_at == later


def test_validate_session_treats_exact_expiry_instant_as_already_expired(
    pg_conn: psycopg.Connection[Any],
) -> None:
    """validate_session's comparison is `expires_at <= now`, so the boundary
    instant itself (now == expires_at) is expired, not valid -- an
    exclusive-on-the-valid-side boundary, the opposite of what a reader
    might assume without checking. This pins the behavior actually
    implemented, not the inclusive boundary a first guess might expect."""
    user = create_user(pg_conn, "boundary@example.com", "a-real-password", FIXED_NOW)
    token = create_session(pg_conn, user, FIXED_NOW)
    exactly_at_expiry = FIXED_NOW + timedelta(days=SESSION_LIFETIME_DAYS)
    with pytest.raises(InvalidSessionError):
        validate_session(pg_conn, token, exactly_at_expiry)


def test_delete_session_invalidates_it_immediately(pg_conn: psycopg.Connection[Any]) -> None:
    user = create_user(pg_conn, "logout@example.com", "a-real-password", FIXED_NOW)
    token = create_session(pg_conn, user, FIXED_NOW)
    delete_session(pg_conn, token)
    with pytest.raises(InvalidSessionError):
        validate_session(pg_conn, token, FIXED_NOW)


def test_delete_session_is_idempotent(pg_conn: psycopg.Connection[Any]) -> None:
    # No explicit assertion beyond "does not raise" -- pytest already fails
    # this test if delete_session raises, which is the actual claim being
    # tested (deleting a token that was never issued, or is already gone,
    # must not be an error). Calling it twice proves the second call is
    # exactly as safe as the first -- the literal meaning of "idempotent."
    delete_session(pg_conn, "a-token-that-was-never-issued")
    delete_session(pg_conn, "a-token-that-was-never-issued")


def test_create_session_token_is_never_stored_raw(pg_conn: psycopg.Connection[Any]) -> None:
    user = create_user(pg_conn, "tokencheck@example.com", "a-real-password", FIXED_NOW)
    token = create_session(pg_conn, user, FIXED_NOW)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT token_hash FROM user_session WHERE user_id = %s", (user.user_id,))
        row = cur.fetchone()
    assert row is not None
    assert token != row[0]
    assert token not in row[0]


def test_authenticate_user_succeeds_with_the_right_password(
    pg_conn: psycopg.Connection[Any],
) -> None:
    create_user(pg_conn, "authok@example.com", "the-right-password", FIXED_NOW)
    user = authenticate_user(pg_conn, "authok@example.com", "the-right-password", FIXED_NOW)
    assert user.email == "authok@example.com"


def test_authenticate_user_rejects_wrong_password(pg_conn: psycopg.Connection[Any]) -> None:
    create_user(pg_conn, "authwrong@example.com", "the-right-password", FIXED_NOW)
    with pytest.raises(InvalidCredentialsError):
        authenticate_user(pg_conn, "authwrong@example.com", "totally-wrong", FIXED_NOW)


def test_authenticate_user_rejects_unknown_email(pg_conn: psycopg.Connection[Any]) -> None:
    with pytest.raises(InvalidCredentialsError):
        authenticate_user(pg_conn, "nobody@example.com", "whatever-password", FIXED_NOW)


def test_five_failures_lock_the_account(pg_conn: psycopg.Connection[Any]) -> None:
    create_user(pg_conn, "locked@example.com", "the-right-password", FIXED_NOW)
    for _ in range(THROTTLE_MAX_FAILURES):
        with pytest.raises(InvalidCredentialsError):
            authenticate_user(pg_conn, "locked@example.com", "wrong", FIXED_NOW)
    with pytest.raises(AccountLockedError):
        authenticate_user(pg_conn, "locked@example.com", "the-right-password", FIXED_NOW)


def test_throttling_applies_to_a_nonexistent_email_too(
    pg_conn: psycopg.Connection[Any],
) -> None:
    """Failed attempts against an email with no account must lock out
    identically -- otherwise "does this lock?" becomes an oracle for
    "does this email have an account?"."""
    for _ in range(THROTTLE_MAX_FAILURES):
        with pytest.raises(InvalidCredentialsError):
            authenticate_user(pg_conn, "neveramember@example.com", "wrong", FIXED_NOW)
    with pytest.raises(AccountLockedError):
        authenticate_user(pg_conn, "neveramember@example.com", "wrong", FIXED_NOW)


def test_successful_login_clears_the_failure_count(pg_conn: psycopg.Connection[Any]) -> None:
    create_user(pg_conn, "recovers@example.com", "the-right-password", FIXED_NOW)
    for _ in range(THROTTLE_MAX_FAILURES - 1):
        with pytest.raises(InvalidCredentialsError):
            authenticate_user(pg_conn, "recovers@example.com", "wrong", FIXED_NOW)
    # One more failure would have locked it -- a correct password instead:
    authenticate_user(pg_conn, "recovers@example.com", "the-right-password", FIXED_NOW)
    # And the count is now reset, so a single subsequent failure doesn't lock:
    with pytest.raises(InvalidCredentialsError):
        authenticate_user(pg_conn, "recovers@example.com", "wrong", FIXED_NOW)


def test_lockout_releases_after_the_window(pg_conn: psycopg.Connection[Any]) -> None:
    create_user(pg_conn, "releases@example.com", "the-right-password", FIXED_NOW)
    for _ in range(THROTTLE_MAX_FAILURES):
        with pytest.raises(InvalidCredentialsError):
            authenticate_user(pg_conn, "releases@example.com", "wrong", FIXED_NOW)
    after_lockout = FIXED_NOW + timedelta(minutes=THROTTLE_LOCKOUT_MINUTES + 1)
    user = authenticate_user(
        pg_conn, "releases@example.com", "the-right-password", after_lockout
    )
    assert user.email == "releases@example.com"


def test_failure_counter_resets_after_the_window(pg_conn: psycopg.Connection[Any]) -> None:
    """Failures separated by more than THROTTLE_WINDOW_MINUTES must not
    accumulate toward a lockout -- _record_failed_login resets failed_count
    to 1 (rather than incrementing) once `now - first_failed_at` exceeds the
    window. THROTTLE_MAX_FAILURES - 1 failures at FIXED_NOW would lock on
    one more *only if* the count kept accumulating; because the next
    failure happens after the window, the count resets to 1 instead, so the
    account is not locked and a correct password right after still works."""
    create_user(pg_conn, "windowreset@example.com", "the-right-password", FIXED_NOW)
    for _ in range(THROTTLE_MAX_FAILURES - 1):
        with pytest.raises(InvalidCredentialsError):
            authenticate_user(pg_conn, "windowreset@example.com", "wrong", FIXED_NOW)
    after_window = FIXED_NOW + timedelta(minutes=THROTTLE_WINDOW_MINUTES + 1)
    # If the counter had NOT reset, this would be the 5th failure -> lockout.
    with pytest.raises(InvalidCredentialsError):
        authenticate_user(pg_conn, "windowreset@example.com", "wrong", after_window)
    # Confirms the reset (not just a delayed lockout): the account is not
    # locked, so the correct password succeeds immediately after.
    user = authenticate_user(
        pg_conn, "windowreset@example.com", "the-right-password", after_window
    )
    assert user.email == "windowreset@example.com"


def test_signup_then_login_returns_a_working_token(client: TestClient) -> None:
    signup_res = client.post(
        "/auth/signup", json={"email": "http@example.com", "password": "a-real-password"}
    )
    assert signup_res.status_code == 201
    signup_token = signup_res.json()["token"]

    me_res = client.get("/auth/me", headers={"Authorization": f"Bearer {signup_token}"})
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "http@example.com"

    login_res = client.post(
        "/auth/login", json={"email": "http@example.com", "password": "a-real-password"}
    )
    assert login_res.status_code == 200
    login_token = login_res.json()["token"]
    assert login_token != signup_token  # a fresh session, not the same one


def test_signup_duplicate_email_and_login_wrong_password_give_identical_errors(
    client: TestClient,
) -> None:
    client.post("/auth/signup", json={"email": "dupe@example.com", "password": "a-real-password"})

    dup_res = client.post(
        "/auth/signup", json={"email": "dupe@example.com", "password": "a-different-password"}
    )
    wrong_pw_res = client.post(
        "/auth/login", json={"email": "dupe@example.com", "password": "totally-wrong"}
    )
    unknown_res = client.post(
        "/auth/login", json={"email": "never-signed-up@example.com", "password": "whatever"}
    )

    assert dup_res.status_code == 400
    assert wrong_pw_res.status_code == 401
    assert unknown_res.status_code == 401
    assert dup_res.json()["detail"] == wrong_pw_res.json()["detail"] == unknown_res.json()["detail"]


def test_me_rejects_missing_and_garbage_tokens(client: TestClient) -> None:
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "not-even-bearer-shaped"}).status_code == 401


def test_me_rejects_an_expired_token_over_http(
    client: TestClient, pg_conn: psycopg.Connection[Any]
) -> None:
    """Creates the session directly against auth_view (not through the HTTP
    signup route) using FIXED_NOW (2026-06-01) -- already well over
    SESSION_LIFETIME_DAYS (30 days) in the past relative to real wall-clock
    time, so the token is naturally already-expired by the time the real
    /auth/me route (which reads the real clock) validates it. No need to
    mock datetime.now() to get a genuinely expired token through the actual
    HTTP path.

    pg_conn.commit() is required after each write here: pg_conn holds its
    own open transaction (autocommit=False, per the fixture), and `client`'s
    requests each run on their own fresh connection via get_connection --
    that connection can only see what pg_conn has actually committed. Same
    pattern sim/tests/conftest.py's own synthetic_league_id fixture already
    uses (ingest_league(pg_conn, ...) then pg_conn.commit()).
    """
    user = create_user(pg_conn, "expiredhttp@example.com", "a-real-password", FIXED_NOW)
    pg_conn.commit()
    token = create_session(pg_conn, user, FIXED_NOW)
    pg_conn.commit()

    me_res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 401


def test_logout_then_me_is_401(client: TestClient) -> None:
    signup_res = client.post(
        "/auth/signup", json={"email": "logsout@example.com", "password": "a-real-password"}
    )
    token = signup_res.json()["token"]

    logout_res = client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout_res.status_code == 204

    me_res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 401


def test_logout_is_idempotent_over_http(client: TestClient) -> None:
    res = client.post("/auth/logout", headers={"Authorization": "Bearer never-issued"})
    assert res.status_code == 204


def test_login_returns_429_after_five_failures_and_never_leaks_over_http(
    client: TestClient,
) -> None:
    client.post(
        "/auth/signup", json={"email": "httplocked@example.com", "password": "the-real-password"}
    )
    for _ in range(5):
        res = client.post(
            "/auth/login", json={"email": "httplocked@example.com", "password": "wrong"}
        )
        assert res.status_code == 401
    locked_res = client.post(
        "/auth/login",
        json={"email": "httplocked@example.com", "password": "the-real-password"},
    )
    assert locked_res.status_code == 429


def test_no_plaintext_password_or_raw_token_ever_lands_in_the_database(
    client: TestClient, pg_conn: psycopg.Connection[Any]
) -> None:
    password = "a-very-specific-secret-password-xyz"
    signup_res = client.post(
        "/auth/signup", json={"email": "secretcheck@example.com", "password": password}
    )
    token = signup_res.json()["token"]

    with pg_conn.cursor() as cur:
        cur.execute("SELECT password_hash FROM app_user")
        password_rows = cur.fetchall()
        cur.execute("SELECT token_hash FROM user_session")
        session_rows = cur.fetchall()

    assert not any(password in row[0] for row in password_rows)
    assert not any(token in row[0] for row in session_rows)
    assert not any(token == row[0] for row in session_rows)


def test_login_triggers_a_refresh_for_a_connected_league(
    client: TestClient,
    raw_fixture: dict[str, Any],
    pg_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backdate ingested_at past the cooldown before logging in again --
    connect's own ingest just ran moments ago, and without this the
    login's own refresh attempt would be correctly skipped by the
    cooldown, producing a false negative rather than a real signal."""
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    signup_res = client.post(
        "/auth/signup",
        json={"email": "login-refresh@example.com", "password": "a-real-password"},
    )
    token = signup_res.json()["token"]
    connect_res = client.post(
        "/leagues/connect",
        json={"league_id": raw_fixture["id"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert connect_res.status_code == 200

    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    pg_conn.execute(
        "UPDATE league SET ingested_at = %s WHERE league_id = %s AND season_id = %s",
        (old, raw_fixture["id"], raw_fixture["seasonId"]),
    )
    pg_conn.commit()

    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: raw_fixture)
    login_res = client.post(
        "/auth/login", json={"email": "login-refresh@example.com", "password": "a-real-password"}
    )
    assert login_res.status_code == 200

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ingested_at FROM league WHERE league_id = %s AND season_id = %s",
            (raw_fixture["id"], raw_fixture["seasonId"]),
        )
        row = cur.fetchone()
    assert row is not None
    (ingested_at,) = row
    assert ingested_at > old


def test_login_succeeds_even_when_the_triggered_refresh_fails(
    client: TestClient,
    raw_fixture: dict[str, Any],
    pg_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    signup_res = client.post(
        "/auth/signup",
        json={"email": "login-refresh-fails@example.com", "password": "a-real-password"},
    )
    token = signup_res.json()["token"]
    client.post(
        "/leagues/connect",
        json={"league_id": raw_fixture["id"]},
        headers={"Authorization": f"Bearer {token}"},
    )

    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    pg_conn.execute(
        "UPDATE league SET ingested_at = %s WHERE league_id = %s AND season_id = %s",
        (old, raw_fixture["id"], raw_fixture["seasonId"]),
    )
    pg_conn.commit()

    def _fail(*args: Any, **kwargs: Any) -> Any:
        raise EspnFetchError("simulated ESPN outage")

    monkeypatch.setattr(reingest, "fetch_live_league", _fail)

    login_res = client.post(
        "/auth/login",
        json={"email": "login-refresh-fails@example.com", "password": "a-real-password"},
    )
    assert login_res.status_code == 200
    assert login_res.json()["token"]


def test_login_is_a_no_op_refresh_wise_with_no_connected_league(client: TestClient) -> None:
    client.post(
        "/auth/signup", json={"email": "no-league@example.com", "password": "a-real-password"}
    )
    login_res = client.post(
        "/auth/login", json={"email": "no-league@example.com", "password": "a-real-password"}
    )
    assert login_res.status_code == 200


def test_login_respects_the_cooldown_across_two_rapid_logins(
    client: TestClient,
    raw_fixture: dict[str, Any],
    pg_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    signup_res = client.post(
        "/auth/signup", json={"email": "rapid-login@example.com", "password": "a-real-password"}
    )
    token = signup_res.json()["token"]
    client.post(
        "/leagues/connect",
        json={"league_id": raw_fixture["id"]},
        headers={"Authorization": f"Bearer {token}"},
    )

    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    pg_conn.execute(
        "UPDATE league SET ingested_at = %s WHERE league_id = %s AND season_id = %s",
        (old, raw_fixture["id"], raw_fixture["seasonId"]),
    )
    pg_conn.commit()

    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: raw_fixture)

    first_login = client.post(
        "/auth/login", json={"email": "rapid-login@example.com", "password": "a-real-password"}
    )
    assert first_login.status_code == 200

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ingested_at FROM league WHERE league_id = %s AND season_id = %s",
            (raw_fixture["id"], raw_fixture["seasonId"]),
        )
        row = cur.fetchone()
    assert row is not None
    (after_first,) = row
    assert after_first > old  # the first login's refresh actually ran

    second_login = client.post(
        "/auth/login", json={"email": "rapid-login@example.com", "password": "a-real-password"}
    )
    assert second_login.status_code == 200

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ingested_at FROM league WHERE league_id = %s AND season_id = %s",
            (raw_fixture["id"], raw_fixture["seasonId"]),
        )
        row = cur.fetchone()
    assert row is not None
    (after_second,) = row
    assert after_second == after_first  # the second login's refresh was skipped (cooldown)
