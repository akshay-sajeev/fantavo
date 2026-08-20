# Auto-Refresh on Login and Connect

Date: 2026-08-19
Status: approved, not yet implemented

## Context

The manual refresh feature (Phase 20, `docs/decisions.md`) added
`POST /league/{league_id}/refresh`, letting a user pull fresh ESPN data
and recompute odds on demand. Live-testing that feature against the real
deployed site surfaced a real gap it didn't close: a newly-connected
league has no cached simulation at all until the once-daily Cron job
runs, so the dashboard 404s before it ever renders -- including the
Refresh button itself, which lives inside the same page. A user who just
connected their league has no way to get their first simulation without
waiting for the batch job.

This spec closes that gap, and extends it per an explicit follow-up
request: every login should also get fresh data, not just handle the
first-connection case.

### Constraints inherited from the existing codebase

- `reingest_user` (`sim/api/reingest.py`) and `precompute_league`
  (`sim/api/precompute.py`) do not change -- this is deployment/wiring,
  not new simulation logic.
- `_run_league_refresh` (new, extracted from the existing
  `refresh_league` route body -- see Decisions) must not change that
  route's observable behavior: same cooldown, same status codes, same
  response shape.
- `connect_league` (`sim/api/league_connection_view.py`) already performs
  one live ESPN fetch and a full `ingest_league` as part of connecting
  (verified by reading it) -- calling the reingest half of the refresh
  logic again immediately after would be a redundant second ESPN call for
  data that was just fetched.
- `/sim` runs on Vercel serverless (`docs/decisions.md`'s Vercel
  Serverless Migration entry), which does not reliably support work that
  outlives an HTTP response -- this spec does the refresh synchronously,
  inside the request, rather than depending on unverified
  background-task behavior on that platform.
- `fetch_live_league` (`ingest/espn_client.py`) already has a 30s network
  timeout on every request it makes -- bounds the worst-case added
  latency this spec introduces, it doesn't need its own new timeout.

## Decisions

1. **Synchronous, server-side, inside the request** -- not a client-side
   fire-and-forget call, not a FastAPI `BackgroundTasks` call. Explicitly
   chosen over both alternatives: client-side coordination is real added
   complexity for no benefit here (this was scoped, then declined), and a
   background task's reliability on Vercel's Python serverless runtime is
   exactly the kind of unverified-platform-behavior risk this project has
   already been burned by once (see the Phase 19 `vercel.json` runtime
   error). Login and connect both get real added latency (a live ESPN
   call, bounded by the existing 30s timeout, plus ~0.4s of compute) --
   an accepted, explicit tradeoff, not an oversight.
2. **A shared helper, `_run_league_refresh`, extracted from the existing
   `refresh_league` route body verbatim** -- the route becomes a
   one-line call to it. `login` also calls it, with any raised
   `HTTPException` caught and logged rather than propagated: a refresh
   failure (ESPN down, cooldown, credential error) must never break
   login itself.
3. **`connect_league_route` calls `precompute_league` directly, not
   `_run_league_refresh`** -- `connect_league` already did the one live
   ESPN fetch + ingest this needs; only the precompute half is missing.
   No cooldown check here either: a just-connected league has nothing to
   protect against.
4. **`signup` is untouched.** Nobody has a connected league yet at
   signup time (`connect` is a separate, later step), so
   `get_connection_state` would always return `league_id=None` there --
   any check added would always be a no-op.
5. **No `/web` changes, no response-shape changes.** `AuthResponseOut`
   (login's response) is unchanged; the refresh is a pure server-side
   side effect the client never sees directly (it shows up indirectly,
   as freshly-updated data the next time the dashboard loads). This is a
   deliberate scope boundary, not an oversight -- decision from the
   brainstorm was explicitly to avoid the added complexity of surfacing
   this to the client.

## Scope

### In scope

- `sim/api/app.py`: extract `_run_league_refresh(conn, user_id,
  league_id, now) -> RefreshLeagueResponse` from `refresh_league`'s
  current body; `refresh_league` itself becomes a thin wrapper around it;
  `login` calls it (best-effort) when the user has a connected league;
  `connect_league_route` calls `precompute_league` directly
  (best-effort) after a successful connect.

### Out of scope

- Any change to `signup`, `reingest_user`, `precompute_league`,
  `connect_league`, or the batch Cron jobs.
- Any `/web` change -- no new response fields, no new UI feedback for
  this background refresh.
- Coordinating this new login-triggered refresh against the daily Cron
  job's own reingest/precompute pass -- same accepted, already-documented
  risk class as the manual refresh route's own Decision 7 (Phase 20).

## Design

### `_run_league_refresh` (extracted, not new logic)

```python
def _run_league_refresh(
    conn: psycopg.Connection[Any], user_id: int, league_id: int, now: datetime
) -> RefreshLeagueResponse:
    """Shared by the manual POST /league/{id}/refresh route and the
    best-effort auto-refresh login triggers (see docs/decisions.md).
    Raises HTTPException for the cooldown (429) and hard-error (502/500)
    cases -- callers that want those to actually reach the client (the
    manual route) let it propagate; callers that want this to be
    best-effort (login) catch HTTPException and log it instead."""
    season_id = league_connection_view.resolve_current_season_id(now)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT ingested_at FROM league WHERE league_id = %s AND season_id = %s",
            (league_id, season_id),
        )
        row = cur.fetchone()
    if row is not None:
        elapsed = now - row[0]
        if elapsed < REFRESH_COOLDOWN:
            retry_after = REFRESH_COOLDOWN - elapsed
            raise HTTPException(
                status_code=429,
                detail="refreshed too recently, try again shortly",
                headers={"Retry-After": str(max(1, int(retry_after.total_seconds())))},
            )

    try:
        reingest_user(conn, user_id, now)
    except CredentialEncryptionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except EspnFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except IngestError:
        return RefreshLeagueResponse(status="ok", ingested_at=None, odds_updated=False)

    try:
        precompute_league(conn, league_id, season_id, now)
    except (LeagueNotIngestedError, *_DATA_UNAVAILABLE_ERRORS):
        return RefreshLeagueResponse(status="ok", ingested_at=now, odds_updated=False)

    return RefreshLeagueResponse(status="ok", ingested_at=now, odds_updated=True)
```

This is a verbatim lift of `refresh_league`'s current body (lines
1424-1466 in `sim/api/app.py` as of Phase 20's final review fix) -- only
`_owner.user_id` becomes the `user_id` parameter. No behavior changes for
the existing route.

### `refresh_league` (route, now thin)

```python
@app.post("/league/{league_id}/refresh", response_model=RefreshLeagueResponse)
def refresh_league(
    league_id: int,
    _owner: auth_view.AuthedUser = Depends(require_league_owner),
    conn: psycopg.Connection[Any] = Depends(get_connection),
) -> RefreshLeagueResponse:
    """Manual, on-demand counterpart to the daily Cron-triggered
    /internal/reingest + /internal/precompute pair -- re-ingests and
    recomputes odds for exactly the caller's one connected league."""
    return _run_league_refresh(conn, _owner.user_id, league_id, datetime.now(UTC))
```

### `login` (auto-refresh, best-effort)

```python
@app.post("/auth/login", response_model=AuthResponseOut)
def login(
    body: LoginRequest,
    conn: psycopg.Connection[Any] = Depends(get_connection),
) -> AuthResponseOut:
    now = datetime.now(UTC)
    try:
        user = auth_view.authenticate_user(conn, body.email, body.password, now)
    except auth_view.AccountLockedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except auth_view.InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=_GENERIC_AUTH_ERROR) from exc
    token = auth_view.create_session(conn, user, now)

    state = league_connection_view.get_connection_state(conn, user.user_id)
    if state.league_id is not None:
        try:
            _run_league_refresh(conn, user.user_id, state.league_id, now)
        except HTTPException as exc:
            # Best-effort: a stale cooldown, an ESPN outage, or a
            # credential problem must never fail login itself. Logged so
            # an operator can still see it happening.
            logger.info(
                "login-triggered refresh skipped for user_id=%s: %s",
                user.user_id, exc.detail,
            )

    return AuthResponseOut(token=token, user_id=user.user_id, email=user.email)
```

### `connect_league_route` (precompute only, no reingest)

```python
@app.post("/leagues/connect", response_model=ConnectLeagueResponseOut)
def connect_league_route(
    body: ConnectLeagueRequest,
    user: auth_view.AuthedUser = Depends(require_user),
    conn: psycopg.Connection[Any] = Depends(get_connection),
) -> ConnectLeagueResponseOut:
    now = datetime.now(UTC)
    try:
        teams = league_connection_view.connect_league(
            conn, user.user_id, body.league_id, body.espn_s2, body.swid, now
        )
    except league_connection_view.LeagueConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CredentialEncryptionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    season_id = league_connection_view.resolve_current_season_id(now)
    try:
        precompute_league(conn, body.league_id, season_id, now)
    except (LeagueNotIngestedError, *_DATA_UNAVAILABLE_ERRORS) as exc:
        # e.g. a not-yet-drafted new season -- connect_league() above
        # already succeeded and committed the ingest; there's just
        # nothing to simulate yet. Never fails the connect response.
        logger.info(
            "post-connect precompute skipped for league_id=%s: %s",
            body.league_id, exc,
        )

    return ConnectLeagueResponseOut(
        teams=[TeamOptionOut(team_id=t.team_id, name=t.name) for t in teams]
    )
```

No cooldown check here -- `connect_league` just committed a fresh ingest
moments ago in this same request, so there is nothing for a cooldown to
protect against.

## Testing

- `sim/tests/test_api_auth.py` (existing file, new tests): a login for a
  user with a connected league triggers a real refresh (DB-observable:
  `league.ingested_at` advances, `read_cached_simulation` returns a
  fresh row); login still returns 200 with a valid token when the
  triggered refresh fails (monkeypatch `fetch_live_league` to raise
  `EspnFetchError`); login is a no-op refresh-wise for a user with no
  connected league (no crash, no unnecessary DB writes); two logins
  seconds apart only refresh once (the second's `league.ingested_at`
  matches the first's, proving the cooldown blocked it -- both logins
  still return 200).

  **Test-setup gotcha, worth calling out explicitly:** since this same
  spec makes `/leagues/connect` also trigger a precompute (and connect's
  own `ingest_league` call sets `ingested_at` to "now"), a test that
  connects a league via `connect_as` and then immediately logs in again
  would have the login's own refresh attempt silently (and correctly)
  skipped by the cooldown -- `ingested_at` from the connect moments ago
  is still fresh. Any test asserting "login itself advanced
  `ingested_at`" must first backdate `league.ingested_at` past
  `REFRESH_COOLDOWN` (same technique `test_api_refresh.py` already uses)
  before calling `/auth/login`, or it will observe a false negative that
  looks like a bug but is actually the cooldown working as designed.
- `sim/tests/test_api_league_connection.py` (existing file, new tests):
  connecting a league leaves a real cached simulation immediately
  (`read_cached_simulation` returns a non-`None` row right after
  `POST /leagues/connect`, no separate precompute step needed --
  the actual regression test for the bug this spec fixes); connect still
  returns 200 with the team list when the post-connect precompute fails
  (a pre-draft-shaped payload, same fixture-mutation technique
  `test_api_precompute.py` already uses).
- `sim/tests/test_api_app.py`'s existing `refresh_league` tests
  (`test_api_refresh.py`) must keep passing unmodified -- proves the
  extraction into `_run_league_refresh` didn't change that route's
  observable behavior.

## Verification

- `pytest sim/tests ingest/tests -q`, `mypy --strict sim`,
  `ruff check sim` -- same bar as every prior phase.
- `npx tsc --noEmit`, `npx eslint .`, `npm run build` from `/web` -- no
  `/web` changes expected, re-verify anyway.
- Live re-verification against the real deployed site: connect a brand
  new league and confirm the dashboard renders immediately (no manual
  `/internal/precompute` trigger needed, unlike the gap this spec
  fixes); log out and back in and confirm `league.ingested_at` advances
  each time (subject to the cooldown).

## Known gaps (accepted, documented deliberately)

- Login and connect both get real added latency (bounded by
  `fetch_live_league`'s existing 30s timeout) -- an explicit, chosen
  tradeoff over the more complex client-side alternative.
- No coordination against the daily Cron job's own reingest/precompute
  pass -- same accepted risk class as Phase 20's own Decision 7.
