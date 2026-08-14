"""Accounts, sessions, and login throttling for Fantavo -- Auth Phase A
(see docs/superpowers/specs/2026-08-14-auth-phase-a-identity-design.md).

No HTTP here (that's sim/api/app.py's 4 new routes) and no ESPN credentials
(that's Phase B) -- this module owns exactly: password hashing/verification,
session token issuance/validation, and login throttling, each backed by one
of the three tables db/migrations/0003_create_auth.sql adds.

Every public write function takes an explicit `now: datetime` argument,
never reading the wall clock itself -- the same discipline CLAUDE.md
requires of stochastic functions taking an explicit `rng`, applied here to
time instead of randomness, and for the identical reason: it is what makes
session-expiry and throttle-window behavior testable without sleeping or
patching the clock (see ingest/db.py's `ingested_at` convention, which this
mirrors).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# NIST SP 800-63B favors length over mandatory character-class mixing --
# this is the only password policy, deliberately no uppercase/digit/symbol
# requirements.
MIN_PASSWORD_LENGTH = 10

# Sliding expiry: every successful validate_session() call pushes
# expires_at forward by this much again.
SESSION_LIFETIME_DAYS = 30

# 5 failures within a 15-minute window locks the email for 15 minutes.
# Module-level constants, an operational choice -- the same class of
# decision as sim.api.scheduler.PRECOMPUTE_INTERVAL_HOURS, not a fitted or
# modelled value.
THROTTLE_MAX_FAILURES = 5
THROTTLE_WINDOW_MINUTES = 15
THROTTLE_LOCKOUT_MINUTES = 15

_hasher = PasswordHasher()


class EmailAlreadyRegisteredError(ValueError):
    """Raised by create_user when email_norm already exists. The route
    handler in sim.api.app maps this to the exact same generic error text
    login's unknown-email/wrong-password cases use -- see the design doc's
    "Uniform errors" section for why this must never reveal that an account
    already exists."""


class InvalidCredentialsError(ValueError):
    """Raised by authenticate_user for an unknown email or a wrong password
    -- deliberately the same exception (and therefore the same HTTP
    response) for both cases, per the same uniform-errors rule."""


class AccountLockedError(ValueError):
    """Raised by authenticate_user, via _raise_if_locked, when this email
    has failed login too many times recently. Mapped to HTTP 429."""


class InvalidSessionError(ValueError):
    """Raised by validate_session for a token that matches no non-expired
    session. Mapped to HTTP 401."""


@dataclass(frozen=True)
class AuthedUser:
    user_id: int
    email: str


def normalize_email(email: str) -> str:
    """The one place email normalization happens -- every lookup and every
    write goes through this, so app_user.email_norm and login_throttle's key
    can never drift out of sync with each other."""
    return email.strip().lower()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    return True


def _hash_token(token: str) -> str:
    """sha256 of a session token -- see user_session.token_hash's docstring
    in the migration for why the raw token itself is never stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validate_email_format(email: str) -> None:
    if "@" not in email or len(email.strip()) < 3:
        raise ValueError("that doesn't look like a valid email address")


def create_user(
    conn: psycopg.Connection[Any], email: str, password: str, created_at: datetime
) -> AuthedUser:
    """Creates a new account. Raises a plain ValueError for a too-short
    password or malformed email (safe to show verbatim -- neither leaks
    anything about other accounts), and EmailAlreadyRegisteredError for a
    duplicate email (which the route handler in sim.api.app deliberately
    does NOT show verbatim -- see that exception's own docstring)."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    _validate_email_format(email)

    email_norm = normalize_email(email)
    password_hash = hash_password(password)

    with conn.transaction(), conn.cursor() as cur:
        try:
            cur.execute(
                """
                INSERT INTO app_user (email, email_norm, password_hash, created_at)
                VALUES (%s, %s, %s, %s)
                RETURNING user_id
                """,
                (email, email_norm, password_hash, created_at),
            )
        except psycopg.errors.UniqueViolation as exc:
            raise EmailAlreadyRegisteredError(
                "an account with this email already exists"
            ) from exc
        row = cur.fetchone()
        assert row is not None
        user_id: int = row[0]

    return AuthedUser(user_id=user_id, email=email)


def create_session(conn: psycopg.Connection[Any], user: AuthedUser, now: datetime) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    expires_at = now + timedelta(days=SESSION_LIFETIME_DAYS)
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO user_session (token_hash, user_id, created_at, expires_at, last_seen_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (token_hash, user.user_id, now, expires_at, now),
        )
    return token


def validate_session(
    conn: psycopg.Connection[Any], token: str, now: datetime
) -> AuthedUser:
    """Sliding expiry: a successful validation here pushes expires_at
    forward another SESSION_LIFETIME_DAYS, so an active user's session
    never lapses mid-use."""
    token_hash = _hash_token(token)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.user_id, u.email, s.expires_at
            FROM user_session s
            JOIN app_user u ON u.user_id = s.user_id
            WHERE s.token_hash = %s
            """,
            (token_hash,),
        )
        row = cur.fetchone()

    if row is None or row[2] <= now:
        raise InvalidSessionError("session is invalid or expired")

    new_expires_at = now + timedelta(days=SESSION_LIFETIME_DAYS)
    with conn.transaction():
        conn.execute(
            "UPDATE user_session SET last_seen_at = %s, expires_at = %s WHERE token_hash = %s",
            (now, new_expires_at, token_hash),
        )

    return AuthedUser(user_id=row[0], email=row[1])


def delete_session(conn: psycopg.Connection[Any], token: str) -> None:
    """Idempotent: deleting a token that was never issued (or was already
    deleted) is not an error -- logout must always succeed from the
    caller's point of view."""
    token_hash = _hash_token(token)
    with conn.transaction():
        conn.execute("DELETE FROM user_session WHERE token_hash = %s", (token_hash,))


def _raise_if_locked(conn: psycopg.Connection[Any], email_norm: str, now: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT locked_until FROM login_throttle WHERE email_norm = %s", (email_norm,)
        )
        row = cur.fetchone()
    if row is not None and row[0] is not None and row[0] > now:
        raise AccountLockedError("too many failed login attempts -- try again later")


def _record_failed_login(
    conn: psycopg.Connection[Any], email_norm: str, now: datetime
) -> None:
    """Reads the current count/window in Python and writes the new state
    back explicitly, rather than a single clever SQL upsert -- plain
    arithmetic over an already-known row, matching this codebase's general
    preference (e.g. sim.api.roster_view's _positional_concentration) for
    Python-side logic over SQL cleverness."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT failed_count, first_failed_at FROM login_throttle WHERE email_norm = %s",
            (email_norm,),
        )
        row = cur.fetchone()

    if row is None:  # noqa: SIM114
        failed_count = 1
        first_failed_at = now
    elif (now - row[1]) > timedelta(minutes=THROTTLE_WINDOW_MINUTES):
        failed_count = 1
        first_failed_at = now
    else:
        failed_count = row[0] + 1
        first_failed_at = row[1]

    locked_until = (
        now + timedelta(minutes=THROTTLE_LOCKOUT_MINUTES)
        if failed_count >= THROTTLE_MAX_FAILURES
        else None
    )

    with conn.transaction():
        conn.execute(
            """
            INSERT INTO login_throttle (email_norm, failed_count, first_failed_at, locked_until)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (email_norm) DO UPDATE SET
                failed_count = EXCLUDED.failed_count,
                first_failed_at = EXCLUDED.first_failed_at,
                locked_until = EXCLUDED.locked_until
            """,
            (email_norm, failed_count, first_failed_at, locked_until),
        )


def _clear_throttle(conn: psycopg.Connection[Any], email_norm: str) -> None:
    with conn.transaction():
        conn.execute("DELETE FROM login_throttle WHERE email_norm = %s", (email_norm,))


def authenticate_user(
    conn: psycopg.Connection[Any], email: str, password: str, now: datetime
) -> AuthedUser:
    """Raises AccountLockedError BEFORE ever checking the password if this
    email is currently locked out -- a locked account's 6th attempt must
    not verify the password at all, correct or not. Raises
    InvalidCredentialsError, identically, for both an unknown email and a
    known email with the wrong password -- see that exception's docstring
    for why they must not be distinguishable."""
    email_norm = normalize_email(email)
    _raise_if_locked(conn, email_norm, now)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, email, password_hash FROM app_user WHERE email_norm = %s",
            (email_norm,),
        )
        row = cur.fetchone()

    if row is not None and verify_password(password, row[2]):
        _clear_throttle(conn, email_norm)
        return AuthedUser(user_id=row[0], email=row[1])

    _record_failed_login(conn, email_norm, now)
    raise InvalidCredentialsError("invalid email or password")
