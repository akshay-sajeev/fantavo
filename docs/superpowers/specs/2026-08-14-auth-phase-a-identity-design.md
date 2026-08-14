# Auth Phase A — Identity

Date: 2026-08-14
Status: approved, not yet implemented

## Context

Fantavo today has no authentication of any kind. `/` redirects to a
`DEFAULT_LEAGUE_ID` environment variable (see `docs/decisions.md`, Phase 5b:
"No league-picker or auth flow"), and every page under `/league/[leagueId]`
is fully generic on that route parameter with zero access control — anyone
who can reach the app can read any league id it has ingested.

The eventual goal is that a user creates an account, logs in, and connects
their own ESPN league. That whole arc decomposes into two phases:

- **Phase A (this spec) — Identity.** Accounts, sessions, route protection.
- **Phase B (separate spec) — League connection.** Encrypted per-user ESPN
  credentials, a live ESPN fetch path, the connect-league flow, and
  user↔league ownership.

Phase A is specified and built first because it stands alone, is verifiable
end-to-end on its own, and de-risks Phase B (which is the larger and more
security-sensitive half).

### Constraints inherited from the existing codebase

- **Python owns all database access.** Every migration, every table, every
  query today goes through `psycopg` from `/ingest` and `/sim`. Nothing in
  `/web` touches Postgres.
- **`/web` is UI only** (CLAUDE.md), and `web/lib/api.ts` — marked
  `import "server-only"` — is the single path from the web layer to the sim
  API.
- **The sim API is internal.** It binds to `127.0.0.1:8123` and is not
  publicly reachable; only the Next.js server calls it.
- **Timestamps are caller-supplied, never `DEFAULT now()`.** `ingest/db.py`
  established this explicitly so idempotency and behavior are testable
  without freezing the system clock. Auth follows the same rule.
- **Secrets discipline.** CLAUDE.md already forbids logging `espn_s2`/`SWID`,
  writing them to fixtures, or including them in error messages or test
  files. Phase A extends that same rule, verbatim, to passwords and session
  tokens.

## Decisions taken before this spec

These were settled during design and are recorded here so the reasoning is
not lost:

1. **Audience: the author plus a handful of friends.** Not a public product.
   This justifies skipping email verification, password reset, and OAuth for
   now, but does *not* justify skipping password hashing, session hygiene, or
   login throttling — the app may still sit on a publicly reachable URL.
2. **Private ESPN leagues must eventually be supported**, which is what
   forces per-user encrypted credentials in Phase B, which in turn is what
   forces this decision:
3. **Auth lives in the Python/FastAPI layer, not Next.js.** The deciding
   factor is that ESPN credentials must be decryptable by the Python ingest
   path (it is what calls ESPN), and Python already owns 100% of DB access.
   Putting auth in Next.js would fork data ownership across two languages and
   require a cross-language-compatible crypto scheme for Phase B. Next.js
   holds an httpOnly session cookie and forwards it; it never reads the
   database.
4. **Signup is open** (no invite code), identified by email + password.

## Scope

### In scope

- `app_user`, `user_session`, and `login_throttle` tables (one migration).
- Signup, login, logout, and current-user endpoints on the sim API.
- A reusable `require_user()` FastAPI dependency (the hook Phase B needs).
- Session cookie handling and route protection in the web layer.
- `/login` and `/signup` pages, and a header control showing the signed-in
  user with a logout action.
- Brute-force throttling on login.

### Out of scope for Phase A

- ESPN credentials, live ESPN fetching, league connection, user↔league
  ownership, per-league authorization. **All Phase B.**
- Password reset and email verification — both require outbound email, which
  this project has no infrastructure for. Deliberately deferred; the schema
  stores an email specifically so these remain possible without a migration.
- OAuth / social login, 2FA.
- Per-IP throttling (see "Known gaps").

## Data model

New migration: `db/migrations/0003_create_auth.sql`.

```sql
-- "user" is a reserved word in Postgres, hence app_user.
CREATE TABLE IF NOT EXISTS app_user (
    user_id       BIGSERIAL PRIMARY KEY,
    -- As the user typed it, for display.
    email         TEXT NOT NULL,
    -- Lowercased/trimmed, the actual lookup key. Separate column rather than
    -- a functional index so the normalization rule lives in exactly one
    -- place (auth_view._normalize_email) and is visible in the schema.
    email_norm    TEXT NOT NULL UNIQUE,
    -- argon2id output. Never the password itself, in any form.
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS user_session (
    -- sha256 of the opaque session token, NOT the token. A database leak
    -- must not hand an attacker live sessions -- the same reasoning that
    -- makes password_hash a hash.
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
-- failed attempts against addresses that do not exist must be throttled
-- identically to ones that do, otherwise lockout behavior itself becomes a
-- user-enumeration oracle.
CREATE TABLE IF NOT EXISTS login_throttle (
    email_norm      TEXT PRIMARY KEY,
    failed_count    INTEGER NOT NULL,
    first_failed_at TIMESTAMPTZ NOT NULL,
    locked_until    TIMESTAMPTZ
);
```

Every timestamp is written by the application from an explicit
caller-supplied value, matching `ingest/db.py`'s documented rule. No
`DEFAULT now()` anywhere.

## Sim API surface

New module `sim/api/auth_view.py`, wired into `sim/api/app.py` alongside the
existing view modules.

| Endpoint | Request | Success | Failure |
|---|---|---|---|
| `POST /auth/signup` | `{email, password}` | `201` + `{token, user_id, email}` | `400` generic |
| `POST /auth/login` | `{email, password}` | `200` + `{token, user_id, email}` | `401` generic, `429` when throttled |
| `POST /auth/logout` | bearer token | `204` | `204` (idempotent) |
| `GET /auth/me` | bearer token | `200` + `{user_id, email}` | `401` |

Signup returns a session token, so a successful signup lands the user
already signed in rather than bouncing them to a separate login step.

The token travels between web and sim as an `Authorization: Bearer <token>`
header, not a cookie — the sim API has no notion of browsers, and the cookie
is purely a web-layer concern.

### `require_user()` dependency

A FastAPI dependency that reads the bearer token, hashes it, looks up a
non-expired `user_session`, refreshes `last_seen_at`, and returns the
`AuthedUser`. Raises `401` otherwise. Phase A does not apply it to any
league route (those still serve `DEFAULT_LEAGUE_ID` data), but it is written
and tested here because it is exactly the hook Phase B attaches per-league
authorization to.

### Session semantics

- Token: `secrets.token_urlsafe(32)`.
- Stored as `sha256(token)` hex. Lookup is by hash, so the raw token exists
  only in transit and in the browser cookie.
- Lifetime: 30 days, sliding. Each successful validation refreshes
  `last_seen_at` and extends `expires_at`.
- Logout deletes the row, so revocation is immediate and real (this is the
  concrete advantage of server-side sessions over a stateless JWT, and the
  reason for the choice).

## Web layer

Route Handlers, following the pattern
`web/app/api/league/[leagueId]/analyst/[teamId]/route.ts` already
established for proxying to the sim API:

- `web/app/api/auth/signup/route.ts`
- `web/app/api/auth/login/route.ts`
- `web/app/api/auth/logout/route.ts`

Each calls sim through `lib/api.ts` and then sets or clears the session
cookie. The token is never returned to client-side JavaScript.

Supporting pieces:

- **`web/lib/auth.ts`** (`import "server-only"`) — `getCurrentUser()` reads
  the cookie and calls `GET /auth/me`, returning the user or `null`.
- **`web/middleware.ts`** (new file — none exists today) — redirects
  requests without a session cookie to `/login`. It protects `/league/:path*`
  and nothing else; `/login`, `/signup`, `/api/auth/*`, and Next's static
  assets must stay reachable while signed out, or login itself breaks. This
  is a cheap first gate only; the authoritative check is always sim
  validating the token, because a cookie's mere presence proves nothing.
- **`web/app/login/page.tsx`** and **`web/app/signup/page.tsx`** — forms
  styled to match the rest of the app.
- **`web/components/ui/input.tsx`** — a minimal text-input primitive. One
  does not exist today (the analyst chat hand-rolls a styled `<input>`), and
  these two forms need several fields, so it is worth adding as a primitive
  following the same shape as the existing `button.tsx` / `card.tsx`. The
  analyst chat is deliberately **not** refactored onto it in this phase —
  that is unrelated churn.
- **Header** (`web/app/layout.tsx`) — shows the signed-in email and a logout
  control when a session exists; shows nothing extra when logged out, so
  `/login` and `/signup` render cleanly.

### Cookie attributes

`httpOnly`, `SameSite=Lax`, `Path=/`, `Secure` when not in development,
`Max-Age` matching the 30-day session lifetime. `httpOnly` is what keeps the
token out of reach of any client-side script; `SameSite=Lax` is the CSRF
mitigation for the state-changing auth routes.

## Security decisions

- **argon2id** via `argon2-cffi`, OWASP's current recommendation, using the
  library's default parameters. Not bcrypt, not any bare SHA construction.
- **Uniform errors.** Signup with an already-registered email, login with an
  unknown email, and login with a wrong password all return the same generic
  message. None of the three reveals whether an address has an account.
- **Throttled uniformly.** Failed logins increment `login_throttle` keyed by
  `email_norm` whether or not that user exists, so lockout cannot be used to
  enumerate accounts either.
- **No secret ever leaves the process.** Passwords and raw session tokens are
  never logged, never placed in an error message or exception string, never
  written to a fixture, and never hardcoded in a test as a value that also
  appears in the database. This is CLAUDE.md's existing `espn_s2`/`SWID` rule
  applied to the new secret types, and it is asserted by tests, not merely
  documented.
- **Password policy:** minimum 10 characters, no composition rules. This
  follows NIST SP 800-63B, which favors length and explicitly discourages
  mandatory character-class mixing. A single module-level constant.

## Login throttling

On a failed login for `email_norm`:

1. Upsert `login_throttle`, incrementing `failed_count`.
2. Once `failed_count >= 5` within a 15-minute window measured from
   `first_failed_at`, set `locked_until = now + 15 minutes`.
3. While `locked_until` is in the future, `/auth/login` returns `429` without
   ever verifying the password.

On a successful login, the row is deleted, so a legitimate user who mistypes
a few times and then succeeds carries no penalty forward.

The window and threshold are module-level constants, documented as
operational choices — the same class of decision as
`PRECOMPUTE_INTERVAL_HOURS` in `sim/api/scheduler.py`, not fitted or modelled
values.

## Testing

New `sim/tests/test_api_auth.py`, following the existing fast-unit-plus-thin-
integration split the other `sim.api` test modules use:

- Signup creates a user; a duplicate email is rejected.
- Email normalization: `Foo@Bar.com` and `foo@bar.com` are the same account.
- Login succeeds with the right password, fails with the wrong one.
- A password below the minimum length is rejected.
- `GET /auth/me` returns the user for a valid token and `401` for a garbage,
  expired, or logged-out token.
- Logout invalidates the session immediately and is idempotent.
- Session expiry is honored (exercised by passing an explicit past timestamp,
  which the caller-supplied-timestamp design makes possible without sleeping
  or patching the clock).
- Throttling: five failures lock the account; the sixth attempt returns `429`
  *without* verifying the password; a successful login clears the counter;
  throttling applies identically to a non-existent email.
- **Secret-leak assertions:** after signup and login, the plaintext password
  and the raw session token appear nowhere in `app_user` or `user_session`.

`require_user()` is tested directly against a throwaway protected route so
Phase B inherits a dependency that is already proven.

## Verification

Per CLAUDE.md ("Always run `make test && make typecheck` before considering a
task done"), and matching how every prior phase in `docs/decisions.md`
reported its verification:

- `pytest` — all existing tests (244 at time of writing) still green, plus
  the new auth tests.
- `mypy --strict sim ingest` — no new errors (there are 21 pre-existing ones
  in `sim/engine.py`, unrelated).
- `ruff check` — clean on new and touched files.
- `npx tsc --noEmit` and `npx eslint .` — clean in `/web`.
- Live browser verification of the real flow: sign up, land logged in, log
  out, get bounced from `/league/...` to `/login`, log back in, reach the
  league pages again.

## State of the app after Phase A

A user can sign up, log in, and log out. `/league/*` requires a session.
League data still comes from `DEFAULT_LEAGUE_ID` — every signed-in user sees
the same league. That is the intended intermediate state; Phase B replaces
it with real connected leagues.

`/` redirects to `/login` when signed out and to the default league when
signed in, preserving today's behavior for authenticated users.

## Known gaps (accepted, documented deliberately)

- **Throttling is per-email, not per-IP.** A distributed attack spreading
  attempts across many addresses is not slowed. Per-IP throttling behind a
  proxy requires careful `X-Forwarded-For` handling and is not worth the
  complexity at this scale, but this is a real limitation, not an oversight.
- **`login_throttle` can be grown by an attacker** submitting failures for
  arbitrary addresses. Bounded in practice at this scale; a periodic cleanup
  of rows whose `locked_until` has long passed is the fix if it ever matters.
- **No password reset.** A user who forgets their password needs manual
  intervention until email infrastructure exists.
- **The sim API trusts that it is unreachable from outside.** It binds to
  localhost and has no auth of its own beyond these endpoints. If it is ever
  exposed publicly, that assumption has to be revisited.
