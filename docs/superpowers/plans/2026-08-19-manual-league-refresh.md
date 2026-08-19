# Manual League Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual, per-user "refresh" action for a connected league — a new `POST /league/{league_id}/refresh` sim route (re-ingest + recompute odds, 5-minute cooldown) and a button on the web dashboard that calls it.

**Architecture:** The backend reuses `reingest_user` and `precompute_league` exactly as they already exist (no changes to either), wrapped in one new authenticated, cooldown-gated route. The frontend follows this codebase's existing thin-proxy pattern (`web/lib/api.ts` → Route Handler → Client Component) with one small, generically-useful extension to `ApiError` to carry `Retry-After` through cleanly.

**Tech Stack:** FastAPI/psycopg3 (backend), Next.js App Router/TypeScript (frontend). No new dependencies.

## Global Constraints

- `reingest_user` (`sim/api/reingest.py`) and `precompute_league` (`sim/api/precompute.py`) do not change — this plan only calls them.
- The new sim route is gated by the existing `require_league_owner` dependency, exactly like every other `/league/{league_id}/*` route.
- Cooldown is 5 minutes, keyed on `(league_id, season_id)` via `league.ingested_at` — not per-user.
- `web/lib/api.ts`: every existing function returns the raw API response type and throws `ApiError` for non-2xx. The new code must fit this exactly — no new return-type patterns.
- No new toast/notification component. Feedback is inline button state only.
- The button renders only on `web/app/league/[leagueId]/page.tsx` (the dashboard/Overview page), not in the shared per-league layout.
- Tests that hit a real route must assert a real, DB-observable side effect, never a mock call-count — matching this repo's established convention (see `sim/tests/test_api_precompute.py`/`test_reingest.py`).
- mypy `--strict` and ruff must stay clean on every Python file touched; `npx tsc --noEmit` and `npx eslint .` clean on every TypeScript file touched.

---

## Task 1: Backend — `POST /league/{league_id}/refresh`

**Files:**
- Modify: `sim/api/app.py` (imports ~line 48, 57-58, 102-103; new model + constant after `LeagueConnectionOut`, ~line 1113-1117; new route after `get_leagues_me`, ~line 1394-1397)
- Test: `sim/tests/test_api_refresh.py` (new)

**Interfaces:**
- Consumes: `reingest_user(conn: psycopg.Connection[Any], user_id: int, now: datetime) -> None` (`sim/api/reingest.py`, unchanged); `precompute_league(conn: psycopg.Connection[Any], league_id: int, season_id: int, computed_at: datetime, n_sims: int = PRECOMPUTE_N_SIMS) -> int` (`sim/api/precompute.py`, unchanged); `league_connection_view.resolve_current_season_id(now: datetime) -> int` (unchanged, already imported as the `league_connection_view` module); `require_league_owner`, `get_connection` (unchanged).
- Produces: `POST /league/{league_id}/refresh` — 429 with a `Retry-After` header (seconds, integer) if cooled down; 403 via `require_league_owner` for a non-owner; 502 for `EspnFetchError`; 500 for `CredentialEncryptionError`; 200 with `RefreshLeagueResponse{status: "ok", ingested_at: datetime | None, odds_updated: bool}` otherwise (`ingested_at` is `None` and `odds_updated` is `False` only when re-ingest itself raised `IngestError`, e.g. a not-yet-drafted new season). Later tasks call this route by URL string only (no Python import), so no other in-repo consumer needs these exact names — but keep them exact, they're what the frontend's test walkthrough hits.

- [ ] **Step 1: Write the failing tests**

Create `sim/tests/test_api_refresh.py`:

```python
"""Tests for POST /league/{league_id}/refresh -- the manual, on-demand
counterpart to the daily Cron-triggered /internal/reingest +
/internal/precompute pair (see docs/decisions.md's Vercel Serverless
Migration entry, and docs/superpowers/specs/2026-08-19-manual-league-
refresh-design.md). Same assertion style as sim/tests/test_api_precompute.py
and test_reingest.py -- real DB-observable side effects, not mock
call-counts."""

from __future__ import annotations

import copy
import os
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import DEFAULT_TEST_DSN
from ingest.espn_client import EspnFetchError
from sim.api import app as app_module
from sim.api import reingest
from sim.api.cache import read_cached_simulation
from sim.tests.conftest import ConnectedClient

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)


@pytest.fixture()
def client(
    pg_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Same shape as sim/tests/test_api_app.py's own `client` fixture."""
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    monkeypatch.setattr(app_module, "start_scheduler", lambda dsn=None: None)
    with TestClient(app_module.app) as test_client:
        yield test_client


def test_refresh_requires_league_ownership(
    connect_as: Callable[[dict[str, Any]], ConnectedClient], raw_fixture: dict[str, Any]
) -> None:
    # require_league_owner runs before any of this route's own logic --
    # an arbitrary league_id this caller never connected 403s immediately,
    # never reaching the cooldown check or reingest_user.
    cc = connect_as(raw_fixture)
    response = cc.client.post("/league/424242/refresh", headers=cc.headers)
    assert response.status_code == 403


def test_refresh_requires_auth(client: TestClient) -> None:
    response = client.post("/league/424242/refresh")
    assert response.status_code == 401


def test_refresh_blocks_a_second_call_within_the_cooldown(
    connect_as: Callable[[dict[str, Any]], ConnectedClient],
    raw_fixture: dict[str, Any],
) -> None:
    # connect_as's own POST /leagues/connect already performed a real
    # ingest moments ago (real wall-clock time), so league.ingested_at is
    # already well within the 5-minute cooldown window -- no extra setup
    # needed to prove the very next refresh call is blocked. The route
    # returns 429 before ever calling reingest_user, so no need to
    # monkeypatch reingest.fetch_live_league here.
    cc = connect_as(raw_fixture)
    response = cc.client.post(f"/league/{cc.league_id}/refresh", headers=cc.headers)
    assert response.status_code == 429
    retry_after = int(response.headers["Retry-After"])
    assert 0 < retry_after <= 300


def test_refresh_succeeds_past_the_cooldown_and_updates_the_cache(
    connect_as: Callable[[dict[str, Any]], ConnectedClient],
    raw_fixture: dict[str, Any],
    pg_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: raw_fixture)
    cc = connect_as(raw_fixture)

    # Backdate ingested_at past the cooldown window -- the real
    # DB-observable precondition the route's cooldown check reads.
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    pg_conn.execute(
        "UPDATE league SET ingested_at = %s WHERE league_id = %s AND season_id = %s",
        (old, raw_fixture["id"], raw_fixture["seasonId"]),
    )
    pg_conn.commit()

    response = cc.client.post(f"/league/{cc.league_id}/refresh", headers=cc.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["odds_updated"] is True
    assert body["ingested_at"] is not None

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ingested_at FROM league WHERE league_id = %s AND season_id = %s",
            (raw_fixture["id"], raw_fixture["seasonId"]),
        )
        row = cur.fetchone()
    assert row is not None
    (ingested_at,) = row
    assert ingested_at > old

    cached = read_cached_simulation(pg_conn, raw_fixture["id"], raw_fixture["seasonId"])
    assert cached is not None
    assert cached.n_sims > 0


def test_refresh_maps_espn_fetch_error_to_502(
    connect_as: Callable[[dict[str, Any]], ConnectedClient],
    raw_fixture: dict[str, Any],
    pg_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: raw_fixture)
    cc = connect_as(raw_fixture)

    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    pg_conn.execute(
        "UPDATE league SET ingested_at = %s WHERE league_id = %s AND season_id = %s",
        (old, raw_fixture["id"], raw_fixture["seasonId"]),
    )
    pg_conn.commit()

    def _fail(*args: Any, **kwargs: Any) -> Any:
        raise EspnFetchError("simulated ESPN outage")

    monkeypatch.setattr(reingest, "fetch_live_league", _fail)

    response = cc.client.post(f"/league/{cc.league_id}/refresh", headers=cc.headers)
    assert response.status_code == 502


def test_refresh_returns_ok_with_odds_not_updated_for_a_not_yet_drafted_season(
    connect_as: Callable[[dict[str, Any]], ConnectedClient],
    raw_fixture: dict[str, Any],
    pg_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors sim/tests/test_api_precompute.py's
    test_precompute_all_leagues_skips_a_league_with_no_drafted_roster
    fixture-mutation technique: a pre-draft-shaped payload (real
    scoringSettings/schedule, drafted forced False, rosters cleared) makes
    reingest_user's own ingest_league call raise RosterNotAvailableError
    (a subclass of IngestError) -- a legitimate state (e.g. a new NFL
    season that hasn't drafted yet), not a server failure."""
    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: raw_fixture)
    cc = connect_as(raw_fixture)

    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    pg_conn.execute(
        "UPDATE league SET ingested_at = %s WHERE league_id = %s AND season_id = %s",
        (old, raw_fixture["id"], raw_fixture["seasonId"]),
    )
    pg_conn.commit()

    pre_draft_raw = copy.deepcopy(raw_fixture)
    pre_draft_raw["draftDetail"]["drafted"] = False
    for team in pre_draft_raw["teams"]:
        team["roster"]["entries"] = []
    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: pre_draft_raw)

    response = cc.client.post(f"/league/{cc.league_id}/refresh", headers=cc.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["odds_updated"] is False
    assert body["ingested_at"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest sim/tests/test_api_refresh.py -v`
Expected: every test fails with a 404 (route doesn't exist yet).

- [ ] **Step 3: Add the imports**

In `sim/api/app.py`:

Line 48, extend the `datetime` import:
```python
from datetime import UTC, datetime, timedelta
```

Between line 57 (`from ingest.errors import IngestError`) and line 58 (`from sim.api import auth_view, league_connection_view`), insert a new line (alphabetically between `ingest.errors` and `sim.api`):
```python
from ingest.espn_client import EspnFetchError
```

Line 102, extend:
```python
from sim.api.precompute import precompute_all_leagues, precompute_league
```

Line 103, extend:
```python
from sim.api.reingest import reingest_all_connected_users, reingest_user
```

- [ ] **Step 4: Add `RefreshLeagueResponse` and `REFRESH_COOLDOWN`**

In `sim/api/app.py`, insert immediately after `LeagueConnectionOut`'s closing line (right before `_scheduler: Any = None`):

```python
class RefreshLeagueResponse(BaseModel):
    status: str
    ingested_at: datetime | None
    odds_updated: bool


# 5 minutes: long enough that spamming the button can't meaningfully
# stress ESPN's own (undocumented) rate limits, short enough the button
# never feels broken. Keyed on the league, not the user -- this app's
# shared-league-view model means a second connected user's refresh within
# this window would just repeat the same work, not serve a genuinely
# different need. See docs/superpowers/specs/2026-08-19-manual-league-
# refresh-design.md.
REFRESH_COOLDOWN = timedelta(minutes=5)
```

- [ ] **Step 5: Add the route**

In `sim/api/app.py`, insert immediately after `get_leagues_me`'s closing `)` (right before `@app.get("/league/{league_id}/simulation", ...)`):

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
        # e.g. RosterNotAvailableError -- a new NFL season that hasn't
        # drafted yet is a legitimate state, not a failure. Nothing
        # changed, so nothing to precompute either.
        return RefreshLeagueResponse(status="ok", ingested_at=None, odds_updated=False)

    try:
        precompute_league(conn, league_id, season_id, now)
    except IngestError:
        return RefreshLeagueResponse(status="ok", ingested_at=now, odds_updated=False)

    return RefreshLeagueResponse(status="ok", ingested_at=now, odds_updated=True)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest sim/tests/test_api_refresh.py -v`
Expected: PASS (6 tests)

- [ ] **Step 7: Run the full existing suite to confirm nothing regressed**

Run: `pytest sim/tests ingest/tests -q`
Expected: PASS, all tests.

- [ ] **Step 8: Typecheck and lint**

Run: `mypy --strict sim` and `ruff check sim`
Expected: no new errors beyond this repo's existing pre-existing baseline.

- [ ] **Step 9: Commit**

```bash
git add sim/api/app.py sim/tests/test_api_refresh.py
git commit -m "sim: add POST /league/{league_id}/refresh (manual re-ingest + recompute)"
```

---

## Task 2: Frontend — API client, types, and Route Handler

**Files:**
- Modify: `web/lib/types.ts` (append `RefreshLeagueResponse`, end of file)
- Modify: `web/lib/api.ts` (`ApiError` gains `retryAfterSeconds`; `authedFetch`'s error branch reads the `Retry-After` header; new `postLeagueRefresh`, appended after `getLeaguesMe`)
- Create: `web/app/api/league/[leagueId]/refresh/route.ts`

**Interfaces:**
- Consumes: Task 1's `POST /league/{league_id}/refresh` (by URL string, `/league/${leagueId}/refresh`) -- not a Python import, just an HTTP contract: 200 with `{status, ingested_at, odds_updated}`, 429 with a `Retry-After` header, 502/500/403/401 otherwise.
- Produces: `postLeagueRefresh(token: string, leagueId: number): Promise<RefreshLeagueResponse>` (throws `ApiError` for non-2xx, `ApiError.retryAfterSeconds?: number` populated from `Retry-After` when present) for Task 3's Route Handler already built in this task; the Route Handler itself, `POST /api/league/{leagueId}/refresh`, returning JSON `{status, ingested_at, odds_updated}` on success or `{error: string, retry_after_seconds?: number}` on failure (429 responses also carry a real `Retry-After` header) -- this exact shape is what Task 3's Client Component parses.

- [ ] **Step 1: Add `RefreshLeagueResponse` to `web/lib/types.ts`**

Append to the end of the file:

```typescript
/** Mirrors sim.api.app.RefreshLeagueResponse. */
export interface RefreshLeagueResponse {
  status: string;
  ingested_at: string | null;
  odds_updated: boolean;
}
```

- [ ] **Step 2: Extend `ApiError` with `retryAfterSeconds`**

In `web/lib/api.ts`, replace:

```typescript
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
```

with:

```typescript
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly retryAfterSeconds?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
```

- [ ] **Step 3: Read the `Retry-After` header in `authedFetch`**

In `web/lib/api.ts`, inside `authedFetch`, replace:

```typescript
  if (!res.ok) {
    // Named responseBody, not body: this function takes a `body` parameter
    // (the request payload), and shadowing it here is exactly the kind of
    // confusion that turns into a bug later.
    const responseBody = await res.text().catch(() => "");
    let detail = responseBody;
    try {
      const parsed = JSON.parse(responseBody) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // not JSON -- fall through and use the raw body text
    }
    throw new ApiError(res.status, detail || res.statusText);
  }
  return res;
}
```

with:

```typescript
  if (!res.ok) {
    // Named responseBody, not body: this function takes a `body` parameter
    // (the request payload), and shadowing it here is exactly the kind of
    // confusion that turns into a bug later.
    const responseBody = await res.text().catch(() => "");
    let detail = responseBody;
    try {
      const parsed = JSON.parse(responseBody) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // not JSON -- fall through and use the raw body text
    }
    const retryAfterHeader = res.headers.get("Retry-After");
    const retryAfterSeconds = retryAfterHeader ? Number(retryAfterHeader) : undefined;
    throw new ApiError(
      res.status,
      detail || res.statusText,
      Number.isFinite(retryAfterSeconds) ? retryAfterSeconds : undefined,
    );
  }
  return res;
}
```

(This is the one shared helper every existing `authedFetch`-based function in this file uses -- the added field is optional and only populated when the header is present, so every existing caller's behavior is unchanged.)

- [ ] **Step 4: Add `postLeagueRefresh`**

In `web/lib/api.ts`, append after `getLeaguesMe` (the current last function in the file), and add `RefreshLeagueResponse` to the existing `import type { ... } from "@/lib/types"` block at the top of the file (alphabetical, between `PowerRankingRoastResponse` and `RosterResponse` -- "Refresh" sorts before "Roster"):

```typescript
export async function postLeagueRefresh(
  token: string,
  leagueId: number,
): Promise<RefreshLeagueResponse> {
  const res = await authedFetch(`/league/${leagueId}/refresh`, token, "POST");
  return (await res.json()) as RefreshLeagueResponse;
}
```

- [ ] **Step 5: Create the Route Handler**

Create `web/app/api/league/[leagueId]/refresh/route.ts`:

```typescript
import { NextResponse } from "next/server";
import { ApiError, postLeagueRefresh } from "@/lib/api";
import { getCurrentUser, getSessionToken } from "@/lib/auth";
import { ownsLeague } from "@/lib/leagueConnection";

/** Thin pass-through to POST /league/{id}/refresh -- see
 * sim.api.app.refresh_league for the actual work. Mirrors
 * season-replay/route.ts's shape: web-layer getCurrentUser/ownsLeague
 * checks run first, the sim API's require_league_owner is the backstop. */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ leagueId: string }> },
) {
  const { leagueId } = await params;
  const id = Number(leagueId);

  const user = await getCurrentUser();
  if (!user) {
    return NextResponse.json({ error: "not signed in" }, { status: 401 });
  }
  if (!(await ownsLeague(id))) {
    return NextResponse.json({ error: "not authorized for this league" }, { status: 403 });
  }
  const token = (await getSessionToken())!;

  try {
    const result = await postLeagueRefresh(token, id);
    return NextResponse.json(result);
  } catch (error) {
    const status = error instanceof ApiError && error.status >= 400 ? error.status : 502;
    const message = error instanceof Error ? error.message : "Unknown error";
    const retryAfterSeconds = error instanceof ApiError ? error.retryAfterSeconds : undefined;
    return NextResponse.json(
      { error: message, retry_after_seconds: retryAfterSeconds },
      {
        status,
        headers: retryAfterSeconds !== undefined ? { "Retry-After": String(retryAfterSeconds) } : {},
      },
    );
  }
}
```

- [ ] **Step 6: Typecheck and lint**

Run (from `/web`): `npx tsc --noEmit && npx eslint .`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add web/lib/types.ts web/lib/api.ts web/app/api/league/[leagueId]/refresh/route.ts
git commit -m "web: add postLeagueRefresh and the /api/league/[leagueId]/refresh route"
```

---

## Task 3: Frontend — `RefreshButton` and dashboard wiring

**Files:**
- Create: `web/components/dashboard/refresh-button.tsx`
- Modify: `web/app/league/[leagueId]/page.tsx` (header, lines 37-42)

**Interfaces:**
- Consumes: Task 2's `POST /api/league/{leagueId}/refresh` (by URL string, fetched directly -- a Client Component can never import `web/lib/api.ts`, which is `import "server-only"`), returning `{status, ingested_at, odds_updated}` on 200 or `{error, retry_after_seconds?}` on non-200.
- Produces: `RefreshButton({ leagueId: number })`, a self-contained Client Component with no further consumers in this plan.

- [ ] **Step 1: Create the component**

Create `web/components/dashboard/refresh-button.tsx`:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

type RefreshState =
  | { kind: "idle" }
  | { kind: "refreshing" }
  | { kind: "cooldown"; secondsRemaining: number }
  | { kind: "error"; message: string };

const DEFAULT_COOLDOWN_SECONDS = 5 * 60;

function formatCountdown(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/**
 * Manual, on-demand counterpart to the daily Cron-triggered re-ingest +
 * precompute (see docs/decisions.md's Vercel Serverless Migration entry
 * and docs/superpowers/specs/2026-08-19-manual-league-refresh-design.md).
 * Posts to the Route Handler (never lib/api.ts directly -- that's
 * server-only), which forwards to sim's POST /league/{id}/refresh.
 */
export function RefreshButton({ leagueId }: { leagueId: number }) {
  const router = useRouter();
  const [state, setState] = useState<RefreshState>({ kind: "idle" });
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  function startCountdown(seconds: number) {
    if (intervalRef.current) clearInterval(intervalRef.current);
    setState({ kind: "cooldown", secondsRemaining: seconds });
    intervalRef.current = setInterval(() => {
      setState((prev) => {
        if (prev.kind !== "cooldown") return prev;
        if (prev.secondsRemaining <= 1) {
          if (intervalRef.current) clearInterval(intervalRef.current);
          return { kind: "idle" };
        }
        return { kind: "cooldown", secondsRemaining: prev.secondsRemaining - 1 };
      });
    }, 1000);
  }

  async function handleClick() {
    setState({ kind: "refreshing" });
    try {
      const res = await fetch(`/api/league/${leagueId}/refresh`, { method: "POST" });
      const body = await res.json();

      if (res.status === 429) {
        const seconds =
          typeof body.retry_after_seconds === "number"
            ? body.retry_after_seconds
            : DEFAULT_COOLDOWN_SECONDS;
        startCountdown(seconds);
        return;
      }
      if (!res.ok) {
        setState({
          kind: "error",
          message: res.status === 502 ? "Couldn't reach ESPN, try again shortly" : "Refresh failed",
        });
        return;
      }

      router.refresh();
      if (body.ingested_at) {
        startCountdown(DEFAULT_COOLDOWN_SECONDS);
      } else {
        setState({ kind: "idle" });
      }
    } catch {
      setState({ kind: "error", message: "Refresh failed" });
    }
  }

  const disabled = state.kind === "refreshing" || state.kind === "cooldown";
  const label =
    state.kind === "refreshing"
      ? "Refreshing…"
      : state.kind === "cooldown"
        ? `Refresh (${formatCountdown(state.secondsRemaining)})`
        : "Refresh";

  return (
    <div className="flex flex-col items-end gap-1.5">
      <Button
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={handleClick}
        className="cursor-pointer"
      >
        {state.kind === "refreshing" ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        ) : (
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
        )}
        {label}
      </Button>
      {state.kind === "error" && (
        <p className="flex items-center gap-1.5 text-xs text-destructive">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {state.message}
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Wire it into the dashboard header**

In `web/app/league/[leagueId]/page.tsx`, add the import:

```typescript
import { RefreshButton } from "@/components/dashboard/refresh-button";
```

Replace:

```tsx
      <div>
        <h1 className="font-heading text-2xl font-semibold tracking-tight">League Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          League {simulation.league_id} &middot; {simulation.season_id} season &middot; based on{" "}
          {simulation.n_sims.toLocaleString()} simulated seasons
        </p>
      </div>
```

with:

```tsx
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-heading text-2xl font-semibold tracking-tight">League Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            League {simulation.league_id} &middot; {simulation.season_id} season &middot; based on{" "}
            {simulation.n_sims.toLocaleString()} simulated seasons
          </p>
        </div>
        <RefreshButton leagueId={id} />
      </div>
```

- [ ] **Step 3: Typecheck, lint, build**

Run (from `/web`): `npx tsc --noEmit && npx eslint . && npm run build`
Expected: clean.

- [ ] **Step 4: Live walkthrough**

With `uvicorn sim.api.app:app` and `npm run dev` both running locally against a real connected league:
1. Load the dashboard, confirm the `Refresh` button renders next to the header.
2. Click it: confirm it shows `Refreshing…` with a spinner, then either returns to `Refresh (4:59)` counting down, or shows an inline error if ESPN is unreachable.
3. Click again immediately: confirm it's disabled during the countdown (can't double-click past it).
4. Wait out the cooldown (or manually backdate `league.ingested_at` in the local dev DB past 5 minutes): confirm the button re-enables and a second click succeeds.
5. Confirm the dashboard's own numbers (standings, odds) reflect the refresh -- easiest to verify by watching `simulation_cache.computed_at` change in the DB, or by making a small change server-side and confirming it now shows.

- [ ] **Step 5: Commit**

```bash
git add web/components/dashboard/refresh-button.tsx web/app/league/[leagueId]/page.tsx
git commit -m "web: add RefreshButton to the league dashboard"
```

---

## Self-Review Notes (for the plan author, not a task)

- **Spec coverage:** Decision 1 (re-ingest + recompute) -- Task 1's route body. Decision 2 (new route, `require_league_owner`) -- Task 1. Decision 3 (5-minute, league-keyed cooldown) -- Task 1's `REFRESH_COOLDOWN` + cursor query on `league.ingested_at`. Decision 4 (no toast, inline state) -- Task 3's `RefreshState`. Decision 5 (dashboard-only placement) -- Task 3, Step 2. Decision 6 (no reconstructed cooldown on page load) -- Task 3's component starts at `{kind: "idle"}` unconditionally. Decision 7 (no cron coordination) -- nothing to implement, already noted as an accepted gap. Scope's file list -- all 6 files (2 sim, 4 web) covered across the 3 tasks. Testing section's sim test cases -- all 6 present in Task 1, Step 1. The spec's corrected `ApiError.retryAfterSeconds` design (fixed post-brainstorm, before this plan was written) -- Task 2, Steps 2-3.
- **Type consistency check:** `RefreshLeagueResponse` (Python, `sim/api/app.py`) has fields `status: str`, `ingested_at: datetime | None`, `odds_updated: bool` -- serializes to JSON as `{status, ingested_at, odds_updated}`. `RefreshLeagueResponse` (TypeScript, `web/lib/types.ts`) matches field-for-field. `postLeagueRefresh`'s return type matches. The Route Handler passes the object straight through unchanged. `RefreshButton` reads `body.ingested_at`/`body.status`/`body.odds_updated` (implicitly, via `body.ingested_at` truthiness) and `body.retry_after_seconds`/`body.error` from the error shape -- consistent with what the Route Handler actually sends in each branch.
