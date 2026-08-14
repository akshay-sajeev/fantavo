# Auth Phase A (Identity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add accounts, login/logout, sessions, and route protection to Fantavo, with no changes yet to which league data is served (that's Phase B).

**Architecture:** Auth lives entirely in the Python/FastAPI sim service (new `sim/api/auth_view.py` + four new routes in `sim/api/app.py`), because Phase B's per-user ESPN credentials must be decryptable by the same Python process that does ingestion, and Python already owns 100% of database access. Next.js holds an httpOnly session cookie and forwards the raw token to sim as a bearer token via new Route Handlers under `web/app/api/auth/*` — it never touches the database. A cheap `middleware.ts` cookie-presence check gates `/league/*`; the authoritative check (a real call to `GET /auth/me`) lives in `app/league/[leagueId]/layout.tsx`.

**Tech Stack:** FastAPI, psycopg3, argon2-cffi (password hashing), Postgres. Next.js 15 Route Handlers, `next/headers` cookies, React `cache()`.

## Global Constraints

These apply to every task below; not repeated per-task.

- Every timestamp passed to a DB write is an explicit caller-supplied `datetime`, never a server-side `now()` default — matches `ingest/db.py`'s existing rule, and is what makes session-expiry and throttle-window tests possible without sleeping or patching the clock.
- Passwords and raw session tokens are never logged, never placed in an exception message, never written to a fixture, and never hardcoded in a test alongside a value that also appears verbatim in the database. This extends CLAUDE.md's existing `espn_s2`/`SWID` rule.
- Signup with an already-registered email, login with an unknown email, and login with a wrong password all return the exact same generic error text (`"invalid email or password"`) at the same status code. None of the three may reveal whether an email has an account.
- All new Python code passes `mypy --strict sim` with zero new errors (there are 21 pre-existing, unrelated errors in `sim/engine.py` and `sim/tests/test_engine.py` — do not attempt to fix those) and `ruff check sim` clean.
- All new TypeScript passes `npx tsc --noEmit` and `npx eslint .` clean from `/web`.
- Minimum password length is 10 characters, no composition rules (module-level constant, not hardcoded inline).
- `conn.transaction()` (not a bare `conn.execute()` + manual `.commit()`) wraps every write — this is the first set of routes in `sim/api/app.py` that write to Postgres at all (every existing route only reads), so there is no existing write-commit convention to match beyond what `ingest/db.py` already establishes for its own multi-statement transactions.

---

## File Structure

**Backend (new):**
- `db/migrations/0003_create_auth.sql` — `app_user`, `user_session`, `login_throttle` tables.
- `sim/api/auth_view.py` — password hashing, session tokens, throttle logic, all DB reads/writes for auth. No FastAPI/HTTP code.
- `sim/tests/test_api_auth.py` — tests against `auth_view.py` directly and through the HTTP routes.

**Backend (modified):**
- `pyproject.toml` — add `argon2-cffi` as a real dependency (matching the precedent `google-genai` set: an ad-hoc `pip install` for everything else, a `pyproject.toml` entry for a phase's own new, load-bearing dependency).
- `sim/api/app.py` — import `auth_view`, add 4 Pydantic models, add `get_bearer_token`/`require_user` dependencies, add 4 routes.
- `sim/tests/conftest.py` — add the 3 new tables to the per-test truncation list.

**Frontend (new):**
- `web/lib/auth.ts` — `getCurrentUser()`, `SESSION_COOKIE_NAME`. Server-only.
- `web/app/api/auth/signup/route.ts`, `web/app/api/auth/login/route.ts`, `web/app/api/auth/logout/route.ts` — thin proxies to sim, set/clear the cookie.
- `web/components/ui/input.tsx` — new primitive (none exists today).
- `web/components/auth/login-form.tsx`, `web/components/auth/signup-form.tsx` — client components.
- `web/app/login/page.tsx`, `web/app/signup/page.tsx`.
- `web/middleware.ts` — cheap cookie-presence gate on `/league/:path*`.
- `web/components/shared/user-menu.tsx` — header logout control.

**Frontend (modified):**
- `web/lib/types.ts` — add `AuthUser`, `AuthResponse`.
- `web/lib/api.ts` — add `postAuthSignup`, `postAuthLogin`, `postAuthLogout`, `getAuthMe`.
- `web/app/league/[leagueId]/layout.tsx` — add the authoritative auth check.
- `web/app/layout.tsx` — becomes `async`, renders `<UserMenu>` when signed in.

---

### Task 1: Auth schema migration

**Files:**
- Create: `db/migrations/0003_create_auth.sql`
- Test: none (schema-only; verified by the next task's tests actually writing to these tables)

**Interfaces:**
- Produces: tables `app_user(user_id, email, email_norm, password_hash, created_at)`, `user_session(token_hash, user_id, created_at, expires_at, last_seen_at)`, `login_throttle(email_norm, failed_count, first_failed_at, locked_until)`.

- [ ] **Step 1: Write the migration**

```sql
-- Auth: accounts and sessions. "user" is a reserved word in Postgres, hence
-- app_user. Every timestamp here is application-supplied (see
-- ingest/db.py's ingested_at convention) -- never a server-side DEFAULT
-- now(), so tests can exercise expiry/throttle windows deterministically.

CREATE TABLE IF NOT EXISTS app_user (
    user_id       BIGSERIAL PRIMARY KEY,
    -- As the user typed it, for display.
    email         TEXT NOT NULL,
    -- Lowercased/trimmed -- the actual lookup key. A separate column
    -- (rather than a functional unique index) so the normalization rule
    -- stays visible in the schema and lives in exactly one Python function
    -- (auth_view.normalize_email).
    email_norm    TEXT NOT NULL UNIQUE,
    -- argon2id output (auth_view.hash_password). Never the password in any
    -- form.
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS user_session (
    -- sha256 of the opaque session token, NOT the token itself -- a
    -- database leak must not hand over live sessions, the same reasoning
    -- that makes password_hash a hash rather than plaintext.
    token_hash   TEXT PRIMARY KEY,
    user_id      BIGINT NOT NULL
        REFERENCES app_user (user_id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_session_user
    ON user_session (user_id);

-- Keyed by email_norm and deliberately NOT foreign-keyed to app_user:
-- failed attempts against an email with no account must be throttled
-- identically to one that has an account, or the lockout behavior itself
-- becomes a way to learn which emails are registered.
CREATE TABLE IF NOT EXISTS login_throttle (
    email_norm      TEXT PRIMARY KEY,
    failed_count    INTEGER NOT NULL,
    first_failed_at TIMESTAMPTZ NOT NULL,
    locked_until    TIMESTAMPTZ
);
```

- [ ] **Step 2: Apply it and confirm the tables exist**

Run:
```bash
python3 -c "
from ingest.db import connect, run_migrations, DEFAULT_DEV_DSN
conn = connect(DEFAULT_DEV_DSN)
run_migrations(conn)
conn.commit()
with conn.cursor() as cur:
    cur.execute(\"SELECT table_name FROM information_schema.tables WHERE table_name IN ('app_user','user_session','login_throttle') ORDER BY table_name\")
    print(cur.fetchall())
conn.close()
"
```
Expected: `[('app_user',), ('login_throttle',), ('user_session',)]`

- [ ] **Step 3: Commit**

```bash
git add db/migrations/0003_create_auth.sql
git commit -m "Add auth schema: app_user, user_session, login_throttle"
```

---

### Task 2: Install argon2-cffi as a real dependency

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: the `argon2` package importable everywhere in `/sim`.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, find:
```toml
dependencies = [
    "google-genai>=1.0.0",
]
```
Replace with:
```toml
dependencies = [
    "google-genai>=1.0.0",
    # argon2id password hashing for auth (Phase A) -- OWASP's current
    # recommendation over bcrypt. A real dependency entry, not an ad-hoc
    # install, following the exact precedent google-genai set: this is a
    # phase's own new, load-bearing dependency, not part of the pre-Phase-13
    # ad-hoc-installed set this file's own comment above describes.
    "argon2-cffi>=23.1.0",
]
```

- [ ] **Step 2: Install it**

```bash
python3 -m pip install argon2-cffi
```

- [ ] **Step 3: Verify it's importable**

```bash
python3 -c "from argon2 import PasswordHasher; from argon2.exceptions import VerifyMismatchError; print('ok')"
```
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "Add argon2-cffi dependency for password hashing"
```

---

### Task 3: Password hashing and email normalization (pure functions, no DB)

**Files:**
- Create: `sim/api/auth_view.py`
- Test: `sim/tests/test_api_auth.py`

**Interfaces:**
- Produces:
  - `MIN_PASSWORD_LENGTH: int = 10`
  - `normalize_email(email: str) -> str`
  - `hash_password(password: str) -> str`
  - `verify_password(password: str, password_hash: str) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `sim/tests/test_api_auth.py`:

```python
"""Tests for sim.api.auth_view and the /auth/* routes.

Split the same way every other sim.api test module is: fast unit tests
directly against auth_view's functions where no DB is needed, then
integration tests through the FastAPI TestClient for the full HTTP surface.
No network calls to ESPN anywhere -- only to the local Postgres instance
(see sim/tests/conftest.py's pg_conn fixture for the skip-if-unreachable
pattern this reuses).
"""

from __future__ import annotations

from sim.api.auth_view import hash_password, normalize_email, verify_password


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest sim/tests/test_api_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sim.api.auth_view'`

- [ ] **Step 3: Write `sim/api/auth_view.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest sim/tests/test_api_auth.py -v`
Expected: 5 passed

- [ ] **Step 5: Typecheck and lint**

Run: `mypy --strict sim/api/auth_view.py sim/tests/test_api_auth.py && ruff check sim/api/auth_view.py sim/tests/test_api_auth.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add sim/api/auth_view.py sim/tests/test_api_auth.py
git commit -m "Add password hashing and email normalization"
```

---

### Task 4: User creation

**Files:**
- Modify: `sim/api/auth_view.py`
- Modify: `sim/tests/conftest.py`
- Test: `sim/tests/test_api_auth.py`

**Interfaces:**
- Consumes: `normalize_email`, `hash_password`, `MIN_PASSWORD_LENGTH`, `EmailAlreadyRegisteredError`, `AuthedUser` (Task 3).
- Produces: `create_user(conn: psycopg.Connection[Any], email: str, password: str, created_at: datetime) -> AuthedUser`.

- [ ] **Step 1: Add the 3 new tables to the test-truncation list**

In `sim/tests/conftest.py`, find:
```python
# simulation_cache isn't in ingest.db.DATA_TABLES (it's Phase 4's own table,
# not part of ingest's normalized sync), so it's truncated alongside it here.
_ALL_TABLES: tuple[str, ...] = (*DATA_TABLES, "simulation_cache")
```
Replace with:
```python
# simulation_cache and the 3 auth tables aren't in ingest.db.DATA_TABLES
# (none of them are written by an ingest run), so they're truncated
# alongside it here. The auth tables specifically need this: without it,
# a duplicate-email test in one test function would collide with a
# same-email signup in another.
_ALL_TABLES: tuple[str, ...] = (
    *DATA_TABLES,
    "simulation_cache",
    "app_user",
    "user_session",
    "login_throttle",
)
```

- [ ] **Step 2: Write the failing tests**

Add to `sim/tests/test_api_auth.py`:

```python
from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest

from sim.api.auth_view import (
    EmailAlreadyRegisteredError,
    create_user,
)

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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest sim/tests/test_api_auth.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_user'` (and the pre-existing 5 tests from Task 3 still pass)

- [ ] **Step 4: Implement `create_user`**

Add to `sim/api/auth_view.py`. First, extend the imports:

```python
from typing import Any

import psycopg
```

(these two lines already exist from Task 3 — confirm, don't duplicate). Then append:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest sim/tests/test_api_auth.py -v`
Expected: 9 passed

- [ ] **Step 6: Typecheck and lint**

Run: `mypy --strict sim/api/auth_view.py sim/tests/test_api_auth.py sim/tests/conftest.py && ruff check sim/api/auth_view.py sim/tests/test_api_auth.py sim/tests/conftest.py`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add sim/api/auth_view.py sim/tests/test_api_auth.py sim/tests/conftest.py
git commit -m "Add create_user with duplicate-email rejection"
```

---

### Task 5: Sessions — create, validate, delete

**Files:**
- Modify: `sim/api/auth_view.py`
- Test: `sim/tests/test_api_auth.py`

**Interfaces:**
- Consumes: `AuthedUser`, `SESSION_LIFETIME_DAYS`, `InvalidSessionError`, `_hash_token`, `create_user` (Tasks 3-4).
- Produces:
  - `create_session(conn: psycopg.Connection[Any], user: AuthedUser, now: datetime) -> str` (returns the raw token)
  - `validate_session(conn: psycopg.Connection[Any], token: str, now: datetime) -> AuthedUser`
  - `delete_session(conn: psycopg.Connection[Any], token: str) -> None`

- [ ] **Step 1: Write the failing tests**

Add to `sim/tests/test_api_auth.py`:

```python
from datetime import timedelta

from sim.api.auth_view import (
    InvalidSessionError,
    create_session,
    delete_session,
    validate_session,
)


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest sim/tests/test_api_auth.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_session'`

- [ ] **Step 3: Implement sessions**

Append to `sim/api/auth_view.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest sim/tests/test_api_auth.py -v`
Expected: 15 passed

- [ ] **Step 5: Typecheck and lint**

Run: `mypy --strict sim/api/auth_view.py sim/tests/test_api_auth.py && ruff check sim/api/auth_view.py sim/tests/test_api_auth.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add sim/api/auth_view.py sim/tests/test_api_auth.py
git commit -m "Add session create/validate/delete with sliding expiry"
```

---

### Task 6: Login throttling

**Files:**
- Modify: `sim/api/auth_view.py`
- Test: `sim/tests/test_api_auth.py`

**Interfaces:**
- Consumes: `THROTTLE_MAX_FAILURES`, `THROTTLE_WINDOW_MINUTES`, `THROTTLE_LOCKOUT_MINUTES`, `AccountLockedError`, `InvalidCredentialsError`, `normalize_email`, `verify_password`, `AuthedUser` (Tasks 3-5).
- Produces: `authenticate_user(conn: psycopg.Connection[Any], email: str, password: str, now: datetime) -> AuthedUser`.

- [ ] **Step 1: Write the failing tests**

Add to `sim/tests/test_api_auth.py`:

```python
from sim.api.auth_view import (
    AccountLockedError,
    InvalidCredentialsError,
    THROTTLE_MAX_FAILURES,
    authenticate_user,
)


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest sim/tests/test_api_auth.py -v`
Expected: FAIL — `ImportError: cannot import name 'authenticate_user'`

- [ ] **Step 3: Implement throttling and `authenticate_user`**

Append to `sim/api/auth_view.py`:

```python
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

    window_expired = row is None or (now - row[1]) > timedelta(minutes=THROTTLE_WINDOW_MINUTES)
    if window_expired:
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

    if row is None or not verify_password(password, row[2]):
        _record_failed_login(conn, email_norm, now)
        raise InvalidCredentialsError("invalid email or password")

    _clear_throttle(conn, email_norm)
    return AuthedUser(user_id=row[0], email=row[1])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest sim/tests/test_api_auth.py -v`
Expected: 21 passed

- [ ] **Step 5: Typecheck and lint**

Run: `mypy --strict sim/api/auth_view.py sim/tests/test_api_auth.py && ruff check sim/api/auth_view.py sim/tests/test_api_auth.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add sim/api/auth_view.py sim/tests/test_api_auth.py
git commit -m "Add login throttling: 5 failures locks an email for 15 minutes"
```

---

### Task 7: HTTP routes — signup, login, logout, me

**Files:**
- Modify: `sim/api/app.py`
- Test: `sim/tests/test_api_auth.py`

**Interfaces:**
- Consumes: everything from `auth_view` (Tasks 3-6): `AuthedUser`, `create_user`, `authenticate_user`, `create_session`, `validate_session`, `delete_session`, `EmailAlreadyRegisteredError`, `InvalidCredentialsError`, `AccountLockedError`, `InvalidSessionError`.
- Produces (HTTP surface):
  - `POST /auth/signup` `{email, password}` → `201 {token, user_id, email}` | `400`
  - `POST /auth/login` `{email, password}` → `200 {token, user_id, email}` | `401` | `429`
  - `POST /auth/logout` (bearer token) → `204`
  - `GET /auth/me` (bearer token) → `200 {user_id, email}` | `401`
  - `require_user()` FastAPI dependency, returning `auth_view.AuthedUser` — unused by any route yet, but this is the exact hook Phase B's per-league authorization attaches to.

- [ ] **Step 1: Write the failing tests**

Add to `sim/tests/test_api_auth.py`. This file already has a `client` fixture pattern to copy from `sim/tests/test_api_roster.py` — add these imports and fixture at the top of the file (alongside the existing ones), then the tests at the bottom:

```python
import os
from collections.abc import Iterator

from fastapi.testclient import TestClient

from ingest.db import DEFAULT_TEST_DSN
from sim.api import app as app_module

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)


@pytest.fixture()
def client(
    pg_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    monkeypatch.setattr(app_module, "start_scheduler", lambda dsn=None: None)
    with TestClient(app_module.app) as test_client:
        yield test_client


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest sim/tests/test_api_auth.py -v`
Expected: FAIL — `404 Not Found` for `/auth/signup` (route doesn't exist yet)

- [ ] **Step 3: Wire the routes into `sim/api/app.py`**

Add to the imports near the top of `sim/api/app.py` (alongside the other `from sim.api.* import` lines):

```python
from sim.api import auth_view
```

Add `Header` to the existing FastAPI import line. Find:
```python
from fastapi import Depends, FastAPI, HTTPException
```
Replace with:
```python
from fastapi import Depends, FastAPI, Header, HTTPException
```

Add these Pydantic models near the other `*Out`/request models (anywhere before the routes, e.g. just above the `_scheduler: Any = None` line):

```python
class SignupRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponseOut(BaseModel):
    token: str
    user_id: int
    email: str


class MeResponseOut(BaseModel):
    user_id: int
    email: str
```

Add the dependency functions and routes after `get_connection` (right after its definition, before the first `@app.get("/league/...")` route):

```python
# The same generic text for every one of: signup with an email that's
# already registered, login with an email that has no account, login with
# the wrong password. None of the three may be distinguishable -- see
# auth_view.EmailAlreadyRegisteredError's docstring.
_GENERIC_AUTH_ERROR = "invalid email or password"


def get_bearer_token(authorization: str | None = Header(default=None)) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or malformed Authorization header")
    return authorization.removeprefix("Bearer ").strip()


def require_user(
    token: str = Depends(get_bearer_token),
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> auth_view.AuthedUser:
    """Validates the bearer token against a real, non-expired session.
    Not yet attached to any league route in Phase A -- Phase B attaches
    this to per-league authorization."""
    try:
        return auth_view.validate_session(conn, token, datetime.now(UTC))
    except auth_view.InvalidSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.post("/auth/signup", response_model=AuthResponseOut, status_code=201)
def signup(
    body: SignupRequest,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> AuthResponseOut:
    now = datetime.now(UTC)
    try:
        user = auth_view.create_user(conn, body.email, body.password, now)
    except auth_view.EmailAlreadyRegisteredError as exc:
        raise HTTPException(status_code=400, detail=_GENERIC_AUTH_ERROR) from exc
    except ValueError as exc:
        # A too-short password or malformed email -- safe to show verbatim,
        # neither leaks anything about other accounts.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    token = auth_view.create_session(conn, user, now)
    return AuthResponseOut(token=token, user_id=user.user_id, email=user.email)


@app.post("/auth/login", response_model=AuthResponseOut)
def login(
    body: LoginRequest,
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> AuthResponseOut:
    now = datetime.now(UTC)
    try:
        user = auth_view.authenticate_user(conn, body.email, body.password, now)
    except auth_view.AccountLockedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except auth_view.InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=_GENERIC_AUTH_ERROR) from exc
    token = auth_view.create_session(conn, user, now)
    return AuthResponseOut(token=token, user_id=user.user_id, email=user.email)


@app.post("/auth/logout", status_code=204)
def logout(
    token: str = Depends(get_bearer_token),
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> None:
    auth_view.delete_session(conn, token)


@app.get("/auth/me", response_model=MeResponseOut)
def me(user: auth_view.AuthedUser = Depends(require_user)) -> MeResponseOut:  # noqa: B008 (idiomatic FastAPI)
    return MeResponseOut(user_id=user.user_id, email=user.email)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest sim/tests/test_api_auth.py -v`
Expected: 29 passed

- [ ] **Step 5: Run the full existing suite to confirm nothing broke**

Run: `pytest sim/tests ingest/tests -q`
Expected: 244 (pre-existing) + 29 (new) = 273 passed

- [ ] **Step 6: Typecheck and lint**

Run: `mypy --strict sim ingest && ruff check sim ingest`
Expected: the same 21 pre-existing errors in `sim/engine.py`/`sim/tests/test_engine.py`, zero new ones; ruff clean

- [ ] **Step 7: Commit**

```bash
git add sim/api/app.py sim/tests/test_api_auth.py
git commit -m "Add /auth/signup, /auth/login, /auth/logout, /auth/me routes"
```

---

### Task 8: Web types and API client functions

**Files:**
- Modify: `web/lib/types.ts`
- Modify: `web/lib/api.ts`

**Interfaces:**
- Produces:
  - `export interface AuthUser { user_id: number; email: string }`
  - `export interface AuthResponse extends AuthUser { token: string }`
  - `postAuthSignup(email: string, password: string): Promise<AuthResponse>`
  - `postAuthLogin(email: string, password: string): Promise<AuthResponse>`
  - `postAuthLogout(token: string): Promise<void>`
  - `getAuthMe(token: string): Promise<AuthUser>`

- [ ] **Step 1: Add the types**

In `web/lib/types.ts`, add near the top (or wherever other small standalone types live):

```typescript
/** Mirrors sim.api.app.MeResponseOut / sim.api.auth_view.AuthedUser. */
export interface AuthUser {
  user_id: number;
  email: string;
}

/** Mirrors sim.api.app.AuthResponseOut. */
export interface AuthResponse extends AuthUser {
  token: string;
}
```

- [ ] **Step 2: Add the API client functions**

In `web/lib/api.ts`, add the import at the top (extend the existing `import type {...} from "@/lib/types"` block with `AuthResponse, AuthUser,`), then append near the bottom of the file, after the existing `postJson` helper and before/alongside the other exported functions:

```typescript
async function authedFetch(path: string, token: string, method: "GET" | "POST"): Promise<Response> {
  const url = new URL(path, API_BASE);

  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
  } catch (cause) {
    throw new ApiError(
      0,
      `could not reach the sim API at ${API_BASE} -- is uvicorn running? (${String(cause)})`,
    );
  }

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // not JSON -- fall through and use the raw body text
    }
    throw new ApiError(res.status, detail || res.statusText);
  }
  return res;
}

export function postAuthSignup(email: string, password: string): Promise<AuthResponse> {
  return postJson<AuthResponse>("/auth/signup", { email, password });
}

export function postAuthLogin(email: string, password: string): Promise<AuthResponse> {
  return postJson<AuthResponse>("/auth/login", { email, password });
}

export async function postAuthLogout(token: string): Promise<void> {
  await authedFetch("/auth/logout", token, "POST");
}

export async function getAuthMe(token: string): Promise<AuthUser> {
  const res = await authedFetch("/auth/me", token, "GET");
  return (await res.json()) as AuthUser;
}
```

- [ ] **Step 3: Typecheck and lint**

Run (from `/web`): `npx tsc --noEmit && npx eslint lib/types.ts lib/api.ts`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
cd web && git add lib/types.ts lib/api.ts
git commit -m "Add auth types and API client functions"
```

---

### Task 9: `getCurrentUser()` and the 3 auth Route Handlers

**Files:**
- Create: `web/lib/auth.ts`
- Create: `web/app/api/auth/signup/route.ts`
- Create: `web/app/api/auth/login/route.ts`
- Create: `web/app/api/auth/logout/route.ts`

**Interfaces:**
- Consumes: `AuthUser`, `AuthResponse`, `postAuthSignup`, `postAuthLogin`, `postAuthLogout`, `getAuthMe`, `ApiError` (Task 8).
- Produces:
  - `SESSION_COOKIE_NAME: string`
  - `getCurrentUser(): Promise<AuthUser | null>`
  - `POST /api/auth/signup`, `POST /api/auth/login` — each returns `{email}` on success (never the token) and sets the httpOnly cookie
  - `POST /api/auth/logout` — clears the cookie

- [ ] **Step 1: Write `web/lib/auth.ts`**

```typescript
import "server-only";
import { cache } from "react";
import { cookies } from "next/headers";
import { getAuthMe } from "@/lib/api";
import type { AuthUser } from "@/lib/types";

export const SESSION_COOKIE_NAME = "fantavo_session";

/**
 * The authoritative session check: reads the cookie and asks the sim API
 * to validate it via GET /auth/me. Wrapped in React's cache() so the
 * several call sites that need this in one request (the root layout, plus
 * app/league/[leagueId]/layout.tsx's own check) share a single round trip
 * instead of firing it once per call site.
 *
 * Fails closed: any error at all -- an expired/invalid token (401), or the
 * sim API being unreachable (ApiError status 0) -- is treated as "not
 * signed in," never as "signed in." There's nowhere safer to fall back to.
 */
export const getCurrentUser = cache(async (): Promise<AuthUser | null> => {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE_NAME)?.value;
  if (!token) return null;
  try {
    return await getAuthMe(token);
  } catch {
    return null;
  }
});
```

- [ ] **Step 2: Write the signup route handler**

Create `web/app/api/auth/signup/route.ts`:

```typescript
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { ApiError, postAuthSignup } from "@/lib/api";
import { SESSION_COOKIE_NAME } from "@/lib/auth";

// 30 days, matching sim.api.auth_view.SESSION_LIFETIME_DAYS.
const SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30;

/**
 * Orchestration only (CLAUDE.md) -- creates the account via sim, then sets
 * the session cookie here. The raw token is never sent to the browser as
 * JSON, only via the httpOnly Set-Cookie header, so client-side JS can
 * never read it.
 */
export async function POST(request: Request) {
  let body: { email?: string; password?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  if (!body.email || !body.password) {
    return NextResponse.json({ error: "email and password are required" }, { status: 400 });
  }

  try {
    const result = await postAuthSignup(body.email, body.password);
    const cookieStore = await cookies();
    cookieStore.set(SESSION_COOKIE_NAME, result.token, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: SESSION_MAX_AGE_SECONDS,
    });
    return NextResponse.json({ email: result.email });
  } catch (error) {
    const status = error instanceof ApiError && error.status >= 400 ? error.status : 502;
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: message }, { status });
  }
}
```

- [ ] **Step 3: Write the login route handler**

Create `web/app/api/auth/login/route.ts` — identical shape to signup, calling `postAuthLogin` instead:

```typescript
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { ApiError, postAuthLogin } from "@/lib/api";
import { SESSION_COOKIE_NAME } from "@/lib/auth";

const SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30;

export async function POST(request: Request) {
  let body: { email?: string; password?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  if (!body.email || !body.password) {
    return NextResponse.json({ error: "email and password are required" }, { status: 400 });
  }

  try {
    const result = await postAuthLogin(body.email, body.password);
    const cookieStore = await cookies();
    cookieStore.set(SESSION_COOKIE_NAME, result.token, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: SESSION_MAX_AGE_SECONDS,
    });
    return NextResponse.json({ email: result.email });
  } catch (error) {
    const status = error instanceof ApiError && error.status >= 400 ? error.status : 502;
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: message }, { status });
  }
}
```

- [ ] **Step 4: Write the logout route handler**

Create `web/app/api/auth/logout/route.ts`:

```typescript
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { postAuthLogout } from "@/lib/api";
import { SESSION_COOKIE_NAME } from "@/lib/auth";

export async function POST() {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE_NAME)?.value;
  if (token) {
    // Best-effort: even if the sim API call fails (e.g. the session had
    // already expired), still clear the cookie below so the browser is
    // signed out locally either way.
    await postAuthLogout(token).catch(() => {});
  }
  cookieStore.delete(SESSION_COOKIE_NAME);
  return NextResponse.json({ ok: true });
}
```

- [ ] **Step 5: Typecheck and lint**

Run (from `/web`): `npx tsc --noEmit && npx eslint lib/auth.ts app/api/auth`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
cd web && git add lib/auth.ts app/api/auth
git commit -m "Add getCurrentUser and signup/login/logout route handlers"
```

---

### Task 10: `Input` primitive

**Files:**
- Create: `web/components/ui/input.tsx`

**Interfaces:**
- Produces: `Input` — a styled `<input>`, props = `React.ComponentProps<"input">`.

No `Input` primitive exists today (`components/ui/` has only `button.tsx` and `card.tsx`; the analyst chat hand-rolls its own styled `<input>`). This task adds one, matching that existing styling and the shadcn-style primitive shape `button.tsx`/`card.tsx` already establish. The analyst chat is deliberately **not** refactored onto it here — unrelated to this phase.

- [ ] **Step 1: Write the component**

```typescript
import * as React from "react";
import { cn } from "@/lib/utils";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "flex h-9 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
```

- [ ] **Step 2: Typecheck and lint**

Run (from `/web`): `npx tsc --noEmit && npx eslint components/ui/input.tsx`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
cd web && git add components/ui/input.tsx
git commit -m "Add Input primitive"
```

---

### Task 11: Login page

**Files:**
- Create: `web/components/auth/login-form.tsx`
- Create: `web/app/login/page.tsx`

**Interfaces:**
- Consumes: `Input` (Task 10), `Button`, `Card`/`CardContent`/`CardHeader`/`CardTitle`, `getCurrentUser` (Task 9).
- Produces: `LoginForm` component; `/login` route.

- [ ] **Step 1: Write the form**

Create `web/components/auth/login-form.tsx`:

```typescript
"use client";

import { useState, type FormEvent } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/**
 * Posts to /api/auth/login (the Route Handler, not sim directly -- that's
 * what sets the httpOnly cookie). On success, a hard navigation
 * (window.location.href) rather than router.push: this guarantees the
 * root layout's getCurrentUser() re-renders server-side against the cookie
 * that was just set, instead of risking a stale client-cached RSC payload
 * for "/".
 */
export function LoginForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setStatus("loading");
    setErrorMessage(null);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const body = await res.json();
      if (!res.ok) {
        throw new Error(body.error ?? `HTTP ${res.status}`);
      }
      window.location.href = "/";
    } catch (error) {
      setStatus("error");
      setErrorMessage(error instanceof Error ? error.message : "Unknown error");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <label htmlFor="login-email" className="text-sm font-medium text-foreground">
          Email
        </label>
        <Input
          id="login-email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={status === "loading"}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <label htmlFor="login-password" className="text-sm font-medium text-foreground">
          Password
        </label>
        <Input
          id="login-password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={status === "loading"}
        />
      </div>
      {status === "error" && errorMessage && (
        <p className="flex items-center gap-1.5 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          {errorMessage}
        </p>
      )}
      <Button type="submit" disabled={status === "loading"} className="cursor-pointer">
        {status === "loading" && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
        Log in
      </Button>
    </form>
  );
}
```

- [ ] **Step 2: Write the page**

Create `web/app/login/page.tsx`:

```typescript
import Link from "next/link";
import { redirect } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LoginForm } from "@/components/auth/login-form";
import { getCurrentUser } from "@/lib/auth";

export default async function LoginPage() {
  const user = await getCurrentUser();
  if (user) redirect("/");

  return (
    <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 py-12">
      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-xl">Log in</CardTitle>
        </CardHeader>
        <CardContent>
          <LoginForm />
          <p className="mt-4 text-center text-sm text-muted-foreground">
            No account?{" "}
            <Link href="/signup" className="text-primary hover:underline">
              Sign up
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 3: Typecheck and lint**

Run (from `/web`): `npx tsc --noEmit && npx eslint components/auth/login-form.tsx app/login/page.tsx`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
cd web && git add components/auth/login-form.tsx app/login/page.tsx
git commit -m "Add login page"
```

---

### Task 12: Signup page

**Files:**
- Create: `web/components/auth/signup-form.tsx`
- Create: `web/app/signup/page.tsx`

**Interfaces:**
- Consumes: `Input`, `Button`, `Card`/`CardContent`/`CardHeader`/`CardTitle` (Task 10), `getCurrentUser` (Task 9).
- Produces: `SignupForm` component; `/signup` route.

- [ ] **Step 1: Write the form**

Create `web/components/auth/signup-form.tsx` — same shape as `LoginForm`, posting to `/api/auth/signup`, with a hint under the password field naming the minimum length (`sim.api.auth_view.MIN_PASSWORD_LENGTH` is 10 — this is UI copy, not read from the API, so keep it in sync by hand if that constant ever changes):

```typescript
"use client";

import { useState, type FormEvent } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function SignupForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setStatus("loading");
    setErrorMessage(null);
    try {
      const res = await fetch("/api/auth/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const body = await res.json();
      if (!res.ok) {
        throw new Error(body.error ?? `HTTP ${res.status}`);
      }
      window.location.href = "/";
    } catch (error) {
      setStatus("error");
      setErrorMessage(error instanceof Error ? error.message : "Unknown error");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <label htmlFor="signup-email" className="text-sm font-medium text-foreground">
          Email
        </label>
        <Input
          id="signup-email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={status === "loading"}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <label htmlFor="signup-password" className="text-sm font-medium text-foreground">
          Password
        </label>
        <Input
          id="signup-password"
          type="password"
          autoComplete="new-password"
          required
          minLength={10}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={status === "loading"}
        />
        <p className="text-xs text-muted-foreground">At least 10 characters.</p>
      </div>
      {status === "error" && errorMessage && (
        <p className="flex items-center gap-1.5 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          {errorMessage}
        </p>
      )}
      <Button type="submit" disabled={status === "loading"} className="cursor-pointer">
        {status === "loading" && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
        Sign up
      </Button>
    </form>
  );
}
```

- [ ] **Step 2: Write the page**

Create `web/app/signup/page.tsx`:

```typescript
import Link from "next/link";
import { redirect } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SignupForm } from "@/components/auth/signup-form";
import { getCurrentUser } from "@/lib/auth";

export default async function SignupPage() {
  const user = await getCurrentUser();
  if (user) redirect("/");

  return (
    <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 py-12">
      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-xl">Sign up</CardTitle>
        </CardHeader>
        <CardContent>
          <SignupForm />
          <p className="mt-4 text-center text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link href="/login" className="text-primary hover:underline">
              Log in
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 3: Typecheck and lint**

Run (from `/web`): `npx tsc --noEmit && npx eslint components/auth/signup-form.tsx app/signup/page.tsx`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
cd web && git add components/auth/signup-form.tsx app/signup/page.tsx
git commit -m "Add signup page"
```

---

### Task 13: Route protection — middleware and the league layout gate

**Files:**
- Create: `web/middleware.ts`
- Modify: `web/app/league/[leagueId]/layout.tsx`

**Interfaces:**
- Consumes: `getCurrentUser` (Task 9).
- Produces: unauthenticated requests to `/league/*` redirect to `/login`.

- [ ] **Step 1: Write the middleware**

Create `web/middleware.ts` (at the `/web` project root, next to `package.json` — Next.js's required location, not under `app/`):

```typescript
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Duplicated from web/lib/auth.ts's SESSION_COOKIE_NAME rather than
// imported: that module is `import "server-only"`, and Next.js runs
// middleware in the Edge runtime, a separate bundle from the Node.js
// runtime Server Components use -- importing a server-only module here
// would be a build error, not just unnecessary.
const SESSION_COOKIE_NAME = "fantavo_session";

/**
 * A cheap first gate only: checks that the session cookie exists, nothing
 * more. It cannot validate the token against the sim API (adding a network
 * round trip to every single navigation in Edge middleware is not worth
 * it), so an expired or tampered cookie still passes this check -- the
 * authoritative check is app/league/[leagueId]/layout.tsx calling
 * getCurrentUser(), which really does call GET /auth/me.
 */
export function middleware(request: NextRequest) {
  const hasSession = request.cookies.has(SESSION_COOKIE_NAME);
  if (!hasSession) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/league/:path*"],
};
```

- [ ] **Step 2: Add the authoritative check to the league layout**

In `web/app/league/[leagueId]/layout.tsx`, find:

```typescript
import { LeagueNav } from "@/components/shared/league-nav";
import { PageTransition } from "@/components/shared/page-transition";

export default async function LeagueLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ leagueId: string }>;
}) {
  const { leagueId } = await params;
```

Replace with:

```typescript
import { redirect } from "next/navigation";
import { LeagueNav } from "@/components/shared/league-nav";
import { PageTransition } from "@/components/shared/page-transition";
import { getCurrentUser } from "@/lib/auth";

export default async function LeagueLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ leagueId: string }>;
}) {
  // The authoritative check -- middleware.ts only verified the cookie
  // exists; this actually validates it against the sim API.
  const user = await getCurrentUser();
  if (!user) redirect("/login");

  const { leagueId } = await params;
```

(the rest of the file — the returned JSX — is unchanged)

- [ ] **Step 3: Verify with a live browser check**

This is the first task where auth actually blocks something, so verify it end-to-end rather than trusting typecheck alone:

1. Ensure the web dev server is running (`preview_start` with the `web-dev` launch config).
2. In a fresh browser tab with no cookies, navigate to `http://localhost:3000/league/885686492`.
3. Confirm it redirects to `/login`.
4. Confirm `/login` and `/signup` themselves load fine (not redirected).

- [ ] **Step 4: Typecheck and lint**

Run (from `/web`): `npx tsc --noEmit && npx eslint middleware.ts "app/league/[leagueId]/layout.tsx"`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
cd web && git add middleware.ts "app/league/[leagueId]/layout.tsx"
git commit -m "Protect /league/* behind auth"
```

---

### Task 14: Header user control (email + logout)

**Files:**
- Create: `web/components/shared/user-menu.tsx`
- Modify: `web/app/layout.tsx`

**Interfaces:**
- Consumes: `getCurrentUser` (Task 9).
- Produces: `UserMenu({ email }: { email: string })`; the header shows it, right-aligned, only when signed in.

- [ ] **Step 1: Write `UserMenu`**

Create `web/components/shared/user-menu.tsx`:

```typescript
"use client";

import { useState } from "react";
import { LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";

export function UserMenu({ email }: { email: string }) {
  const [loggingOut, setLoggingOut] = useState(false);

  async function handleLogout() {
    setLoggingOut(true);
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
    window.location.href = "/login";
  }

  return (
    <div className="flex items-center gap-3">
      <span className="hidden text-sm text-muted-foreground sm:inline">{email}</span>
      <Button
        variant="ghost"
        size="sm"
        onClick={handleLogout}
        disabled={loggingOut}
        className="cursor-pointer"
      >
        <LogOut className="h-4 w-4" aria-hidden="true" />
        Log out
      </Button>
    </div>
  );
}
```

- [ ] **Step 2: Wire it into the root layout**

In `web/app/layout.tsx`, find:

```typescript
import { TooltipProvider } from "@/components/ui/tooltip";
import { RouteProgress } from "@/components/shared/route-progress";
```

Replace with:

```typescript
import { TooltipProvider } from "@/components/ui/tooltip";
import { RouteProgress } from "@/components/shared/route-progress";
import { UserMenu } from "@/components/shared/user-menu";
import { getCurrentUser } from "@/lib/auth";
```

Find:

```typescript
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
```

Replace with:

```typescript
export default async function RootLayout({ children }: LayoutProps<"/">) {
  const user = await getCurrentUser();
  return (
```

Find:

```typescript
            <div className="flex h-16 items-center justify-start gap-6 px-4">
              <Link
                href="/"
                className="flex items-center gap-3 font-[family-name:var(--font-brand)] text-xl font-bold tracking-wide text-primary uppercase"
              >
                <Image src="/fantavo-logo.svg" alt="" width={40} height={30} className="h-10 w-auto" priority />
                Fantavo
              </Link>
            </div>
```

Replace with:

```typescript
            <div className="flex h-16 items-center justify-between gap-6 px-4">
              <Link
                href="/"
                className="flex items-center gap-3 font-[family-name:var(--font-brand)] text-xl font-bold tracking-wide text-primary uppercase"
              >
                <Image src="/fantavo-logo.svg" alt="" width={40} height={30} className="h-10 w-auto" priority />
                Fantavo
              </Link>
              {user && <UserMenu email={user.email} />}
            </div>
```

(`justify-start` → `justify-between`: with only the logo present this renders identically to before — `justify-between` places a single child at the start exactly like `justify-start` does — and now correctly pushes `UserMenu` to the right when it's present. This does not move the logo, so the sidebar-icon-centered-under-the-logo alignment from earlier work is unaffected.)

- [ ] **Step 3: Verify with a live browser check**

1. Sign up for a new account through `/signup`.
2. Confirm you land on `/` and the header shows your email and a "Log out" button.
3. Click "Log out"; confirm you land on `/login` and a subsequent visit to `/league/885686492` redirects to `/login` again.
4. Log back in through `/login`; confirm you reach the league pages again and the header shows your email.

- [ ] **Step 4: Typecheck and lint**

Run (from `/web`): `npx tsc --noEmit && npx eslint components/shared/user-menu.tsx app/layout.tsx`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
cd web && git add components/shared/user-menu.tsx app/layout.tsx
git commit -m "Show signed-in user and logout control in the header"
```

---

### Task 15: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Full backend test suite**

Run: `pytest sim/tests ingest/tests -q`
Expected: all pass (273: the pre-existing 244 plus this phase's 29)

- [ ] **Step 2: Full backend typecheck and lint**

Run: `mypy --strict sim ingest && ruff check sim ingest db scripts`
Expected: only the 21 pre-existing `sim/engine.py`/`sim/tests/test_engine.py` mypy errors; ruff clean

- [ ] **Step 3: Full frontend typecheck, lint, and build**

Run (from `/web`): `npx tsc --noEmit && npx eslint . && npm run build`
Expected: all clean, production build succeeds

- [ ] **Step 4: Live end-to-end walkthrough in the browser**

With both the sim API and the Next.js dev server running:

1. Fresh browser session, no cookies. Visit `/`. Confirm it ends up at `/login` (via the `/` → `/league/{id}` → middleware → `/login` redirect chain).
2. Sign up with a new email and a password under 10 characters. Confirm a clear, specific error (not the generic one — this isn't an enumeration-risk case).
3. Sign up with a valid password. Confirm you land on `/` signed in, header shows your email.
4. Log out. Confirm you land on `/login` and `/league/{id}` now redirects there too.
5. Attempt to log in with the wrong password 5 times, then a 6th time with the *correct* password. Confirm the 6th attempt is also rejected (429), proving the lockout doesn't just stop after failures — it blocks the correct password too, for the lockout window.
6. Log in with the correct email/password on a fresh browser session (no prior failed attempts). Confirm success.
7. Try signing up again with the same email. Confirm the error message is identical text to a wrong-password login error (open browser devtools Network tab and compare the two response bodies directly, not just eyeballing the UI).

- [ ] **Step 5: Final commit** (only if any fixes were needed above)

```bash
git add -A
git commit -m "Fix issues found during auth Phase A verification"
```
