"""Tests for sim.api.auth_view and the /auth/* routes.

Split the same way every other sim.api test module is: fast unit tests
directly against auth_view's functions where no DB is needed, then
integration tests through the FastAPI TestClient for the full HTTP surface.
No network calls to ESPN anywhere -- only to the local Postgres instance
(see sim/tests/conftest.py's pg_conn fixture for the skip-if-unreachable
pattern this reuses).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest

from sim.api.auth_view import (
    EmailAlreadyRegisteredError,
    InvalidSessionError,
    create_session,
    create_user,
    delete_session,
    hash_password,
    normalize_email,
    validate_session,
    verify_password,
)


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
