# Auto-Refresh on Login and Connect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the "new connection has no cached simulation" dashboard-404 gap, and make every login refresh a connected user's data, by wiring the existing refresh logic into `POST /auth/login` and `POST /leagues/connect`.

**Architecture:** Extract the existing `POST /league/{league_id}/refresh` route's body into a shared helper, `_run_league_refresh`, called by that route (unchanged behavior) and, best-effort, by `login`. `connect_league_route` calls `precompute_league` directly (no reingest — `connect_league` already did one). All synchronous, inside the request — no client-side or background-task changes.

**Tech Stack:** FastAPI/psycopg3. No new dependencies. No `/web` changes.

## Global Constraints

- `reingest_user`, `precompute_league`, `connect_league` do not change.
- `_run_league_refresh` must be a behavior-preserving extraction: the existing `POST /league/{league_id}/refresh` route's tests (`sim/tests/test_api_refresh.py`) must keep passing unmodified.
- Login's response shape (`AuthResponseOut`) does not change — the refresh is a pure server-side side effect.
- A refresh failure (cooldown, ESPN down, credential error) must never cause `login` or `POST /leagues/connect` to fail.
- `connect_league_route`'s new precompute step has no cooldown check — nothing to protect against on a brand-new connection.
- Tests that hit a real route must assert a real, DB-observable side effect, never a mock call-count.
- mypy `--strict` and ruff must stay clean.

---

## Task 1: Extract `_run_league_refresh`, wire into login and connect

**Files:**
- Modify: `sim/api/app.py` (the `refresh_league` route body, ~line 1414-1466; the `login` route, ~line 1322-1335; the `connect_league_route` route, ~line 1351-1373)
- Modify: `sim/tests/test_api_auth.py` (new tests, appended)
- Modify: `sim/tests/test_api_league_connection.py` (new tests, appended)

**Interfaces:**
- Consumes: `reingest_user`, `precompute_league`, `league_connection_view.resolve_current_season_id`, `league_connection_view.get_connection_state`, `REFRESH_COOLDOWN`, `RefreshLeagueResponse`, `_DATA_UNAVAILABLE_ERRORS`, `EspnFetchError`, `CredentialEncryptionError`, `IngestError`, `LeagueNotIngestedError` — all already defined/imported in `sim/api/app.py`, no new imports needed for this task.
- Produces: `_run_league_refresh(conn: psycopg.Connection[Any], user_id: int, league_id: int, now: datetime) -> RefreshLeagueResponse` — raises `HTTPException` for the 429/502/500 cases exactly as `refresh_league` does today. No other task in this plan depends on it (this is the only task).

- [ ] **Step 1: Write the failing tests**

Append to `sim/tests/test_api_auth.py`. First, add two imports at the top of the file (alphabetical, with the existing import block):

```python
from ingest.espn_client import EspnFetchError
from sim.api import league_connection_view, reingest
```

Then append these tests to the end of the file:

```python
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
```

Append to `sim/tests/test_api_league_connection.py`. First, add these two imports (alphabetical, with the existing import block):

```python
import copy
```
(at the top, alphabetical with the other stdlib imports: `json`, `logging`, `os` — `copy` goes before `json`)

```python
from sim.api.cache import read_cached_simulation
```
(alphabetical, after the existing `from sim.api import auth_view, league_connection_view` line)

Then append these tests to the end of the file:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest sim/tests/test_api_auth.py sim/tests/test_api_league_connection.py -v -k "refresh or precompute"`
Expected: the new tests fail (login/connect don't trigger any refresh yet — `ingested_at`/`cached` assertions fail); the two `-fails`/`-no-op` tests may pass trivially since they only assert `status_code == 200`, which is already true today — that's fine, they'll become meaningful once the code exists.

- [ ] **Step 3: Extract `_run_league_refresh`**

In `sim/api/app.py`, replace the `refresh_league` route (currently the entire function body) with a helper plus a thin route. Replace:

```python
@app.post("/league/{league_id}/refresh", response_model=RefreshLeagueResponse)
def refresh_league(
    league_id: int,
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008 (idiomatic FastAPI)
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> RefreshLeagueResponse:
    """Manual, on-demand counterpart to the daily Cron-triggered
    /internal/reingest + /internal/precompute pair (see docs/decisions.md's
    Vercel Serverless Migration entry) -- re-ingests and recomputes odds
    for exactly the caller's one connected league, not the full batch."""
    now = datetime.now(UTC)
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
        reingest_user(conn, _owner.user_id, now)
    except CredentialEncryptionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except EspnFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except IngestError:
        # A parse-time failure inside ingest_league itself (e.g. a
        # malformed/unparseable ESPN payload) -- nothing was ingested, so
        # nothing changed and there's nothing to precompute either. NOT
        # the not-yet-drafted-season case (that one succeeds here and
        # fails in precompute_league below -- see that branch).
        return RefreshLeagueResponse(status="ok", ingested_at=None, odds_updated=False)

    try:
        precompute_league(conn, league_id, season_id, now)
    except (LeagueNotIngestedError, *_DATA_UNAVAILABLE_ERRORS):
        # e.g. RosterNotAvailableError -- a new NFL season that hasn't
        # drafted yet is a legitimate state, not a failure. reingest_user
        # above already succeeded and committed, so ingested_at reflects
        # that; there's just nothing to precompute yet.
        return RefreshLeagueResponse(status="ok", ingested_at=now, odds_updated=False)

    return RefreshLeagueResponse(status="ok", ingested_at=now, odds_updated=True)
```

with:

```python
def _run_league_refresh(
    conn: psycopg.Connection[Any], user_id: int, league_id: int, now: datetime
) -> RefreshLeagueResponse:
    """Shared by the manual POST /league/{id}/refresh route and the
    best-effort auto-refresh login triggers (see docs/decisions.md's
    Auto-Refresh on Login and Connect entry). Raises HTTPException for
    the cooldown (429) and hard-error (502/500) cases -- callers that
    want those to actually reach the client (the manual route) let it
    propagate; callers that want this to be best-effort (login) catch
    HTTPException and log it instead."""
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
        # A parse-time failure inside ingest_league itself (e.g. a
        # malformed/unparseable ESPN payload) -- nothing was ingested, so
        # nothing changed and there's nothing to precompute either. NOT
        # the not-yet-drafted-season case (that one succeeds here and
        # fails in precompute_league below -- see that branch).
        return RefreshLeagueResponse(status="ok", ingested_at=None, odds_updated=False)

    try:
        precompute_league(conn, league_id, season_id, now)
    except (LeagueNotIngestedError, *_DATA_UNAVAILABLE_ERRORS):
        # e.g. RosterNotAvailableError -- a new NFL season that hasn't
        # drafted yet is a legitimate state, not a failure. reingest_user
        # above already succeeded and committed, so ingested_at reflects
        # that; there's just nothing to precompute yet.
        return RefreshLeagueResponse(status="ok", ingested_at=now, odds_updated=False)

    return RefreshLeagueResponse(status="ok", ingested_at=now, odds_updated=True)


@app.post("/league/{league_id}/refresh", response_model=RefreshLeagueResponse)
def refresh_league(
    league_id: int,
    _owner: auth_view.AuthedUser = Depends(require_league_owner),  # noqa: B008 (idiomatic FastAPI)
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> RefreshLeagueResponse:
    """Manual, on-demand counterpart to the daily Cron-triggered
    /internal/reingest + /internal/precompute pair (see docs/decisions.md's
    Vercel Serverless Migration entry) -- re-ingests and recomputes odds
    for exactly the caller's one connected league, not the full batch."""
    return _run_league_refresh(conn, _owner.user_id, league_id, datetime.now(UTC))
```

- [ ] **Step 4: Wire the auto-refresh into `login`**

In `sim/api/app.py`, replace:

```python
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
```

with:

```python
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

    state = league_connection_view.get_connection_state(conn, user.user_id)
    if state.league_id is not None:
        try:
            _run_league_refresh(conn, user.user_id, state.league_id, now)
        except HTTPException as exc:
            # Best-effort: a stale cooldown, an ESPN outage, or a
            # credential problem must never fail login itself. Logged so
            # an operator can still see it happening.
            logger.info(
                "login-triggered refresh skipped for user_id=%s: %s", user.user_id, exc.detail
            )

    return AuthResponseOut(token=token, user_id=user.user_id, email=user.email)
```

- [ ] **Step 5: Wire the post-connect precompute into `connect_league_route`**

In `sim/api/app.py`, replace:

```python
@app.post("/leagues/connect", response_model=ConnectLeagueResponseOut)
def connect_league_route(
    body: ConnectLeagueRequest,
    user: auth_view.AuthedUser = Depends(require_user),  # noqa: B008 (idiomatic FastAPI)
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> ConnectLeagueResponseOut:
    now = datetime.now(UTC)
    try:
        teams = league_connection_view.connect_league(
            conn, user.user_id, body.league_id, body.espn_s2, body.swid, now
        )
    except league_connection_view.LeagueConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CredentialEncryptionError as exc:
        # A server misconfiguration (missing/invalid CREDENTIAL_ENCRYPTION_KEY),
        # not a caller error -- same 500-with-str(exc) shape as
        # AnalystConfigError below. The exception's own message never includes
        # the key or the credential (see sim.api.crypto), so surfacing it is
        # safe and tells the operator exactly what to fix.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ConnectLeagueResponseOut(
        teams=[TeamOptionOut(team_id=t.team_id, name=t.name) for t in teams]
    )
```

with:

```python
@app.post("/leagues/connect", response_model=ConnectLeagueResponseOut)
def connect_league_route(
    body: ConnectLeagueRequest,
    user: auth_view.AuthedUser = Depends(require_user),  # noqa: B008 (idiomatic FastAPI)
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> ConnectLeagueResponseOut:
    now = datetime.now(UTC)
    try:
        teams = league_connection_view.connect_league(
            conn, user.user_id, body.league_id, body.espn_s2, body.swid, now
        )
    except league_connection_view.LeagueConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CredentialEncryptionError as exc:
        # A server misconfiguration (missing/invalid CREDENTIAL_ENCRYPTION_KEY),
        # not a caller error -- same 500-with-str(exc) shape as
        # AnalystConfigError below. The exception's own message never includes
        # the key or the credential (see sim.api.crypto), so surfacing it is
        # safe and tells the operator exactly what to fix.
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # connect_league() above already did the one live ESPN fetch + ingest
    # this needs; only the precompute half is missing, so this calls it
    # directly rather than going through _run_league_refresh (which would
    # redundantly re-fetch from ESPN). No cooldown check either -- a
    # just-connected league has nothing to protect against.
    season_id = league_connection_view.resolve_current_season_id(now)
    try:
        precompute_league(conn, body.league_id, season_id, now)
    except (LeagueNotIngestedError, *_DATA_UNAVAILABLE_ERRORS) as exc:
        # e.g. a not-yet-drafted new season -- connect_league() above
        # already succeeded and committed the ingest; there's just
        # nothing to simulate yet. Never fails the connect response.
        logger.info("post-connect precompute skipped for league_id=%s: %s", body.league_id, exc)

    return ConnectLeagueResponseOut(
        teams=[TeamOptionOut(team_id=t.team_id, name=t.name) for t in teams]
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest sim/tests/test_api_auth.py sim/tests/test_api_league_connection.py -v`
Expected: PASS, all tests in both files (existing + new).

- [ ] **Step 7: Run the full existing suite to confirm the extraction didn't change `refresh_league`'s behavior**

Run: `pytest sim/tests/test_api_refresh.py -v`
Expected: PASS, all 8 tests unmodified — proves `_run_league_refresh` is a behavior-preserving extraction.

Run: `pytest sim/tests ingest/tests -q`
Expected: PASS, all tests (336 existing + this task's new ones).

- [ ] **Step 8: Typecheck and lint**

Run: `mypy --strict sim` and `ruff check sim`
Expected: no new errors beyond this repo's existing pre-existing baseline.

- [ ] **Step 9: Verify `/web` is unaffected**

Run (from `/web`): `npx tsc --noEmit && npx eslint . && npm run build`
Expected: clean (no `/web` files touched by this task).

- [ ] **Step 10: Commit**

```bash
git add sim/api/app.py sim/tests/test_api_auth.py sim/tests/test_api_league_connection.py
git commit -m "sim: auto-refresh on login and connect (extract _run_league_refresh)"
```

---

## Self-Review Notes (for the plan author, not a task)

- **Spec coverage:** Decision 1 (synchronous, server-side) — the whole task is this. Decision 2 (`_run_league_refresh` extraction, login wiring) — Steps 3-4. Decision 3 (connect calls `precompute_league` directly, no reingest, no cooldown) — Step 5. Decision 4 (signup untouched) — nothing to implement, confirmed no task touches `signup`. Decision 5 (no `/web` changes) — Step 9 verifies this explicitly. Scope's file list (`sim/api/app.py` only, plus the two existing test files) — matches. Testing section's cases (login triggers refresh, login succeeds when refresh fails, login no-op with no league, cooldown across two logins, connect precomputes immediately, connect succeeds when precompute can't run) — all six present in Step 1. The spec's explicit test-setup gotcha (backdate `ingested_at` before testing login's own trigger) — baked directly into every relevant test in Step 1, not left as a footnote.
- **Type consistency check:** `_run_league_refresh`'s signature (`conn, user_id, league_id, now`) matches every call site: `refresh_league` passes `(conn, _owner.user_id, league_id, datetime.now(UTC))`; `login` passes `(conn, user.user_id, state.league_id, now)` — both correct per the extraction. `RefreshLeagueResponse` is only ever constructed inside the helper now, never duplicated at either call site.
