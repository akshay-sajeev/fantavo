# Auth Phase B — League Connection

Date: 2026-08-14
Status: approved, not yet implemented

## Context

Phase A (identity — accounts, sessions, `/league/*` route protection) is
merged. It deliberately left every signed-in user seeing the same
`DEFAULT_LEAGUE_ID` league, because connecting a user's *real* ESPN league
was always meant to be its own, separate, more security-sensitive phase (see
`docs/superpowers/specs/2026-08-14-auth-phase-a-identity-design.md`'s
Context section).

This spec is that phase: a signed-in user with no connected league is
directed to a connect flow, submits their ESPN league ID (and, for private
leagues, `espn_s2`/`SWID` session cookies), and from then on sees their own
real league — kept current by a recurring background re-fetch — instead of
the shared default.

### Decisions taken before this spec

1. **Genuinely multi-tenant, not one shared friends' league.** Any signed-up
   user can connect any ESPN league — their own, public or private — with
   their own credentials. This is the harder version of the problem and was
   chosen deliberately over the simpler "everyone is in the same league"
   model.
2. **One league per user account**, not several with a switcher. Simpler
   schema (columns on `app_user`, not a join table) and simpler UI. Multiple
   leagues per user is a later, explicitly separate extension if it's ever
   needed — not designed for here.
3. **The user also identifies which team is theirs** as part of connecting,
   so the dashboard and analyst tools can default to it instead of requiring
   manual team selection on every visit (today's behavior, which stays
   available for looking at other teams in the league).
4. **Re-ingestion is recurring, not one-time.** A connected league is kept
   current automatically through the season via a background job, not just
   fetched once at connect time.

### Constraints inherited from the existing codebase

- **`ingest_league(conn, raw, ingested_at)` (`ingest/db.py`) already accepts
  an in-memory payload dict**, not just a fixture file path — it needs no
  changes for this phase. Everything new is in how `raw` gets produced (live
  ESPN call vs. loading a committed fixture) and who calls it (a request
  handler and a scheduled job, not only a CLI script).
- **"Fixtures, not live calls" (CLAUDE.md) still governs all dev/test work.**
  This phase adds the *first* code that calls the live ESPN API from the
  always-running service. That code is isolated to one module
  (`ingest/espn_client.py`) with an injectable transport specifically so
  tests can substitute a fake and never make a real network call, keeping
  the existing rule intact everywhere else.
- **`require_user()` (Phase A, `sim/api/auth_view.py`)** is the exact hook
  this phase attaches to — built and tested in Phase A for this purpose.
- **APScheduler `BackgroundScheduler` (`sim/api/scheduler.py`)** is the
  existing precedent for recurring work in this single-process service; the
  new re-ingest job follows it rather than introducing a task queue.
- **Timestamps are caller-supplied, never `DEFAULT now()`** — same rule as
  every prior migration.
- **Secrets discipline (CLAUDE.md)** already forbids logging or hardcoding
  `espn_s2`/`SWID`. This phase is where that rule stops being about a
  project-owner's own dev-only cookies and starts being about real users'
  live credentials at rest in production — encryption, not just careful
  logging, is now required.

## Scope

### In scope

- `app_user` schema extension: connected league, season, team, and encrypted
  credentials (one migration).
- `ingest/espn_client.py` — the first reusable, live-capable ESPN HTTP
  client, extracted from `scripts/fetch_fixture.py`'s existing fetch/merge
  logic.
- `POST /leagues/connect`, `POST /leagues/team`, `GET /leagues/me` on the sim
  API.
- Credential encryption at rest (`cryptography`/Fernet, server-held master
  key via `.env`, following `sim/api/env.py`'s existing loader).
- A recurring re-ingest background job (extends `sim/api/scheduler.py`).
- `/connect-league` and `/connect-league/pick-team` web pages, and updated
  root-level redirect logic that replaces `DEFAULT_LEAGUE_ID`.

### Out of scope for Phase B

- Multiple leagues per user, league switching. Deferred per Decision 2 above.
- Disconnecting/reconnecting a league, or changing which team is "mine"
  after the fact. A user who needs this today would need direct DB access;
  a settings page for it is a natural, separate follow-up.
- Surfacing re-ingest health (e.g., "last synced 2 hours ago", or a warning
  when a user's stored cookies have expired) in the UI. The job logs
  failures server-side; nothing is user-visible yet.
- Any change to `sim/engine.py` or the simulation/projection layer itself —
  this phase only changes *which* league's data a user sees, never how it's
  analyzed.

## Data model

New migration: `db/migrations/0004_league_connection.sql`.

```sql
-- Extends app_user (Phase A) rather than a separate join table -- Decision 2
-- fixes this at one league per user, so a 1:1 set of nullable columns is
-- simpler than a table whose only ever purpose is a 1:1 relation. All
-- columns are NULL together until a user completes /leagues/connect; team
-- stays NULL between "league connected" and "team picked" (the two-step
-- part of the flow).
ALTER TABLE app_user
    ADD COLUMN espn_league_id      BIGINT,
    ADD COLUMN espn_season_id      INTEGER,
    ADD COLUMN espn_team_id        INTEGER,
    -- Fernet ciphertext (opaque bytes -- the encryption key never touches
    -- the database, only .env). NULL together for public leagues, which
    -- need no cookies at all (see scripts/fetch_fixture.py's existing
    -- docstring on this point).
    ADD COLUMN espn_s2_encrypted   BYTEA,
    ADD COLUMN espn_swid_encrypted BYTEA,
    ADD COLUMN league_connected_at TIMESTAMPTZ;
```

No foreign key to `league`/`team` — those tables are keyed by
`(league_id, season_id, team_id)` value tuples, ingested independently of
`app_user`. `POST /leagues/team` checks the submitted `team_id` against the
already-ingested `team` table at the application layer before saving it.

Every timestamp is application-supplied, matching the rule every prior
migration in this repo follows.

## `ingest/espn_client.py`

A new module, formalizing what `scripts/fetch_fixture.py` has done ad hoc
since Phase 0: fetch each of the `VIEWS` (`mTeam`, `mRoster`, `mMatchup`,
...) from ESPN's fantasy API for a given `league_id`/`season_id`, merge them
into the single combined payload shape `ingest_league()` expects, using
`espn_s2`/`SWID` cookies when supplied.

```python
def fetch_live_league(
    league_id: int,
    season_id: int,
    espn_s2: str | None,
    swid: str | None,
    transport: requests.Session | None = None,
) -> dict[str, Any]:
    ...
```

`transport` defaults to a real `requests.Session()` but is injectable, so
`sim/tests` can pass a fake one and this module's own tests, and every
caller's tests, never touch the network — the same principle as auth's
injectable clock, applied to HTTP.

`scripts/fetch_fixture.py` is refactored to call this function and then
layer its own scrub-and-write-to-`/fixtures` behavior on top, rather than
duplicating the fetch/merge logic. This is the one pre-existing file this
phase touches outside of its own new code — a targeted deduplication, not a
rewrite; its CLI behavior and output are unchanged.

Unlike `scripts/fetch_fixture.py`'s scrubbed, PII-pseudonymized fixtures
(which get committed to a public git repo), the payload this function
returns is stored as-is in `league.raw_payload` — real teammate names are
the point of connecting a real league, and nothing in the payload ESPN
returns ever includes the requester's own cookies (those are sent, never
echoed back), so there is nothing credential-shaped to scrub from it.

## Credential encryption

New dependency: `cryptography` (added to `pyproject.toml` following the
`argon2-cffi`/`google-genai` precedent of declaring a phase's own load-
bearing dependency explicitly, not installing it ad hoc).

- **Fernet** (AES-128-CBC + HMAC, from `cryptography.fernet`) — symmetric,
  authenticated, and simple; there is no need for asymmetric encryption
  since only the server itself ever decrypts.
- **Master key**: a new `CREDENTIAL_ENCRYPTION_KEY` read via
  `sim/api/env.py`'s existing `.env` loader, alongside `GEMINI_API_KEY`.
  Never stored in the database, never logged.
- `sim/api/auth_view.py` (or a new small `sim/api/crypto.py`, decided during
  implementation planning) gains `encrypt_credential(plaintext) -> bytes`
  and `decrypt_credential(ciphertext) -> str`, used by both the connect
  endpoint and the recurring re-ingest job.
- Plaintext `espn_s2`/`SWID` are never logged, never placed in an error
  message, and never written to a test file as a literal that also appears
  in a fixture or the database — the same rule Phase A asserted for
  passwords and session tokens, extended here by test, not just by
  documentation.

## Sim API surface

New module `sim/api/league_connection_view.py`, wired into `sim/api/app.py`
alongside the existing view modules, every route behind `require_user()`.

| Endpoint | Request | Success | Failure |
|---|---|---|---|
| `POST /leagues/connect` | `{league_id, espn_s2?, swid?}` | `200` + `{teams: [{team_id, name}]}` | `400` with a specific reason (bad league id, expired/wrong cookies, private league missing cookies) |
| `POST /leagues/team` | `{team_id}` | `200` | `400` if `team_id` isn't one of the connected league's real teams |
| `GET /leagues/me` | bearer token | `200` + `{league_id, season_id, team_id, connected_at}` (all `null` if unconnected) | `401` |

`POST /leagues/connect` flow: call `ingest/espn_client.py` with the
*submitted* credentials (not yet saved). On failure, nothing is persisted —
return `400` immediately. On success: encrypt and save the credentials plus
`espn_league_id`/`espn_season_id` onto `app_user`, call the existing
`ingest_league()` with the fetched payload inside the same request, and
return the team list read back from what was just ingested — one live ESPN
call total, no second fetch for the team-picker step.

Unlike Phase A's login/signup, these failure messages *can* be specific:
there's no other account's existence to protect here, only the requesting
user's own attempt.

Current season is used by default — a user connecting a league gets *this*
season, matching what "connect my league" means day-to-day; historical
seasons are out of scope (not requested, and `ingest_league` already
supports them per-season if ever needed later).

## Recurring re-ingest job

Extends `sim/api/scheduler.py` with a second `BackgroundScheduler` job,
`PRECOMPUTE_INTERVAL_HOURS`'s same 6-hour cadence (no reason to invent a
different one): for every `app_user` row with a non-null `espn_league_id`,
decrypt its stored credentials, call `ingest/espn_client.py`, and re-run
`ingest_league()` — already idempotent by design, so a re-run that finds
nothing changed is a safe no-op.

One user's failure (revoked cookies, a transient ESPN error) is caught and
logged per-user (league id only, never credentials) and does not abort the
rest of the batch — the same per-league error isolation
`precompute_all_leagues` already has.

## Web layer

- **`web/app/connect-league/page.tsx`** — league ID field plus optional
  `espn_s2`/`SWID` fields with a hint that private leagues need them and a
  link to where to find them. Submits to a new
  `web/app/api/leagues/connect/route.ts`, following the existing
  route-handler proxy pattern (`web/lib/api.ts`, same JSON-parse guard and
  status mapping as every other route handler).
- **`web/app/connect-league/pick-team/page.tsx`** — the team list returned
  by the connect call, submitted to
  `web/app/api/leagues/team/route.ts`, then redirects into
  `/league/{espn_league_id}`.
- **Root redirect logic** (`web/app/page.tsx`, currently an unconditional
  redirect to `/league/{DEFAULT_LEAGUE_ID}`) now calls `GET /leagues/me`
  first: no connection → `/connect-league`; connected but no team picked →
  `/connect-league/pick-team`; both set → `/league/{their league id}`.
  `DEFAULT_LEAGUE_ID` and its env var are removed entirely once this lands.
- **`web/lib/leagueConnection.ts`** (new, `server-only`, mirrors
  `web/lib/auth.ts`'s shape) — `getLeagueConnection()` wraps
  `GET /leagues/me` for the pages above and for `page.tsx`.

## Testing

New `sim/tests/test_api_league_connection.py` and
`sim/tests/test_espn_client.py`, following the existing fast-unit-plus-thin-
integration split:

- `ingest/espn_client.py`: fetching and merging views into the combined
  payload shape, against a fake transport returning canned responses (no
  real network call) — including a private-league case (cookies present) and
  a public-league case (none needed).
- `POST /leagues/connect`: success persists encrypted credentials and
  ingests the league (assert real rows land in `league`/`team`/etc., same as
  `ingest/tests/test_db.py`'s existing idempotency tests); a bad league id or
  bad cookies persists nothing and returns `400`; the fake ESPN client is
  used throughout, never the real one.
- `POST /leagues/team`: accepts a real team id from the connected league,
  rejects one that isn't.
- `GET /leagues/me`: null fields before connecting, populated after.
- **Secret-leak assertions**: after connect, plaintext `espn_s2`/`SWID`
  appear nowhere in `app_user`, any log output, or any error message —
  mirrors Phase A's equivalent assertion for passwords and session tokens.
- Encryption round-trip: `decrypt_credential(encrypt_credential(x)) == x`,
  plus a test that the stored bytes are not the plaintext.
- Recurring re-ingest job: one user's simulated fetch failure doesn't stop
  the batch, mirroring `precompute_all_leagues`'s existing per-league
  isolation test; a successful run updates `league.ingested_at`.

## Verification

Per CLAUDE.md ("Always run `make test && make typecheck` before considering
a task done"), matching how Phase A and every prior phase reported
verification:

- `pytest` — all existing tests still green, plus the new ones for this
  phase.
- `mypy --strict sim ingest` — no new errors beyond the 21 pre-existing ones
  in `sim/engine.py`.
- `ruff check` — clean on new and touched files.
- `npx tsc --noEmit` and `npx eslint .` — clean in `/web`.
- Live verification against a real ESPN league (not a fixture, since this is
  exactly the live path being built): connect a real public league, confirm
  the team list renders, pick a team, land on `/league/{id}` showing real
  data; repeat with a real private league and real `espn_s2`/`SWID` cookies;
  confirm a wrong league id and expired cookies both fail with a specific,
  non-generic message and persist nothing.

## State of the app after Phase B

A signed-in user with no connected league is routed to `/connect-league`
instead of seeing any default. Once connected, `/` and `/league/*` show
their real ESPN league, kept current automatically every 6 hours. Every
existing analytics feature (dashboard, power rankings, playoff odds,
analyst chat, etc.) is unchanged — it already operates on whatever
`league_id`/`season_id` the route gives it; this phase only changes what
that route parameter actually is for a given signed-in user, and adds a
default team so tools that ask "which team" can pre-select it.

## Known gaps (accepted, documented deliberately)

- **No re-ingest health visibility.** If a user's cookies expire mid-season,
  the recurring job silently stops succeeding for them (logged server-side
  only) until they notice stale data. A "last synced" indicator or an
  expired-credentials banner is a natural follow-up, not built here.
- **No way to disconnect or change league/team from the UI.** Out of scope
  per this spec's Scope section; needs direct DB access today.
- **`espn_s2`/`SWID` cookies themselves expire on ESPN's own schedule**
  (session cookies, not API keys) — this phase stores and re-uses them but
  cannot renew them; a user whose cookies expire must reconnect manually
  once a "disconnect/reconnect" UI exists.
- **One league per user is a real limitation**, not just an MVP simplification
  left for later polish — someone in two real ESPN leagues can only connect
  one of them to fantavo today. Deliberate per Decision 2; revisit if it
  becomes a real complaint.
