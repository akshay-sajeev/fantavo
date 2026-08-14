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
from dataclasses import dataclass

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
