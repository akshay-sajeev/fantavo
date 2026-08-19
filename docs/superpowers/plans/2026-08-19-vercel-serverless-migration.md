# Vercel Serverless Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `/sim`'s deployment target from Railway to Vercel by replacing its in-process APScheduler with two secret-gated HTTP endpoints Vercel Cron Jobs can call, adding a Vercel Python entry point, and retiring `railway.toml`.

**Architecture:** `sim/api/app.py`'s FastAPI app, all ~20 existing routes, and every dependency stay exactly as they are. Three additions make the app deployable on Vercel: (1) `require_cron_secret` + two new `POST /internal/precompute` / `POST /internal/reingest` routes that call the same `precompute_all_leagues`/`reingest_all_connected_users` functions the old scheduler called, (2) a `VERCEL=1`-gated `lifespan()` so local `uvicorn` still gets automatic recurring precompute/reingest while a Vercel deploy doesn't try to start a background thread that can't survive between invocations, (3) `api/index.py` (a 4-line re-export of the existing `app` object) and `vercel.json` at the repo root, matching Vercel's documented Python-runtime/ASGI/Cron-Job conventions.

**Tech Stack:** Python 3.12, FastAPI, psycopg3, pytest + FastAPI's `TestClient`, mypy --strict, ruff. No new dependencies.

## Global Constraints

- `sim/api/app.py`'s FastAPI app, all ~20 routes, `require_user`/`require_league_owner`, and every dependency stay as they are — this is a deployment-mechanics change, not a route-level rewrite.
- `precompute_all_leagues` (`sim/api/precompute.py`) and `reingest_all_connected_users` (`sim/api/reingest.py`) do not change — only how and how often they get invoked changes.
- `get_connection` (`sim/api/app.py`) needs no code change — it already opens one `psycopg.connect(dsn)` per request via a FastAPI dependency, the right shape for pooled serverless Postgres.
- Local development must be unaffected: a developer running `uvicorn sim.api.app:app` as a persistent process must keep getting automatic recurring precompute/reingest with no local cron setup.
- Every stochastic/DB-timestamped call in new code takes an explicit value where the existing functions already support one (`precompute_all_leagues(conn, computed_at)`, `reingest_all_connected_users(conn, now)`) — no new hardcoded/invented values.
- mypy `--strict` and ruff must stay clean on every file touched, including the two new root-level files (`api/index.py`, and by extension `vercel.json` which has no lint surface).
- Tests that hit a real route must assert a real, DB-observable side effect (a `simulation_cache` row, an updated `league.ingested_at`), never a mock call-count — matching this repo's existing `sim/tests/test_api_precompute.py` / `test_reingest.py` style.
- Vercel's exact Python-runtime config keys, the `CRON_SECRET` header/scheme, and the `VERCEL=1` auto-set env var are implemented at best-effort confidence against Vercel's documented conventions and are **not verified against a real deploy** — this project has no Vercel account/CLI set up (confirmed: `vercel` is not installed, no `.vercel/` directory, no existing `web/vercel.json`). Actually creating the Vercel projects, provisioning Vercel Postgres/Neon, and running the real deploy remain manual, user-driven steps, same as every other deployment-account action in this project. Do not attempt to run `vercel deploy` or create Vercel/Neon resources as part of this plan.

---

## Task 1: `require_cron_secret` and the two `/internal/*` endpoints

**Files:**
- Modify: `sim/api/app.py` (imports ~line 93-102; new code inserted after `require_league_owner`, ~line 1220, and after the `health` route, ~line 1233)
- Modify: `.env.example` (append a new documented var, matching the file's existing per-var comment convention)
- Test: `sim/tests/test_api_internal.py` (new)

**Interfaces:**
- Consumes: `precompute_all_leagues(conn: psycopg.Connection[Any], computed_at: datetime | None = None) -> None` (`sim/api/precompute.py`, unchanged); `reingest_all_connected_users(conn: psycopg.Connection[Any], now: datetime | None = None) -> None` (`sim/api/reingest.py`, unchanged); `get_connection` (`sim/api/app.py`, unchanged); `read_cached_simulation` (`sim/api/cache.py`, unchanged, already imported in `app.py`).
- Produces: `require_cron_secret(authorization: str | None = Header(default=None)) -> None` — raises `HTTPException(401)` on a missing/wrong secret, returns `None` on success. Routes `POST /internal/precompute` and `POST /internal/reingest`, both returning `{"status": "ok"}` on success, both depending on `require_cron_secret`. Later tasks don't depend on these directly, but Task 4's `docs/decisions.md` entry describes them.

- [ ] **Step 1: Write the failing tests**

Create `sim/tests/test_api_internal.py`:

```python
"""Tests for the two /internal/* routes (sim/api/app.py) that Vercel Cron
Jobs call in place of sim/api/scheduler.py's in-process interval jobs on a
serverless deploy (see docs/decisions.md's Vercel Serverless Migration
entry). Same assertion style as sim/tests/test_api_precompute.py and
test_reingest.py -- a real DB-observable side effect, not a mock
call-count, proves the underlying function actually ran."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import DEFAULT_TEST_DSN
from sim.api import app as app_module
from sim.api import auth_view, league_connection_view, reingest
from sim.api.cache import read_cached_simulation

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)

# A deliberately old fixed instant for the initial connect -- proves the
# reingest test below actually moved ingested_at forward, rather than just
# finding a row that was already there.
FIXED_CONNECT_AT = datetime(2020, 1, 1, tzinfo=timezone.utc)


@pytest.fixture()
def client(
    pg_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Same shape as sim/tests/test_api_app.py's own `client` fixture --
    see that module's docstring for why the scheduler is stubbed out and
    why `pg_conn` is requested even though unused directly here."""
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    monkeypatch.setattr(app_module, "start_scheduler", lambda dsn=None: None)
    with TestClient(app_module.app) as test_client:
        yield test_client


def test_trigger_precompute_401s_with_no_authorization_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CRON_SECRET", raising=False)
    response = client.post("/internal/precompute")
    assert response.status_code == 401


def test_trigger_precompute_401s_with_the_wrong_secret(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRON_SECRET", "a-real-cron-secret")
    response = client.post(
        "/internal/precompute", headers={"Authorization": "Bearer wrong-secret"}
    )
    assert response.status_code == 401


def test_trigger_precompute_caches_the_synthetic_league(
    client: TestClient,
    pg_conn: psycopg.Connection[Any],
    synthetic_league_id: int,
    raw_fixture: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRON_SECRET", "a-real-cron-secret")
    response = client.post(
        "/internal/precompute", headers={"Authorization": "Bearer a-real-cron-secret"}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    cached = read_cached_simulation(pg_conn, synthetic_league_id, raw_fixture["seasonId"])
    assert cached is not None
    assert cached.n_sims > 0


def test_trigger_reingest_401s_with_no_authorization_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CRON_SECRET", raising=False)
    response = client.post("/internal/reingest")
    assert response.status_code == 401


def test_trigger_reingest_updates_ingested_at_for_a_connected_user(
    client: TestClient,
    pg_conn: psycopg.Connection[Any],
    raw_fixture: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    user = auth_view.create_user(
        pg_conn, "cronreingest@example.com", "a-real-password", FIXED_CONNECT_AT
    )
    league_connection_view.connect_league(
        pg_conn, user.user_id, raw_fixture["id"], None, None, FIXED_CONNECT_AT
    )
    pg_conn.commit()

    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: raw_fixture)
    monkeypatch.setenv("CRON_SECRET", "a-real-cron-secret")

    response = client.post(
        "/internal/reingest", headers={"Authorization": "Bearer a-real-cron-secret"}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ingested_at FROM league WHERE league_id = %s AND season_id = %s",
            (raw_fixture["id"], raw_fixture["seasonId"]),
        )
        row = cur.fetchone()
    assert row is not None
    (ingested_at,) = row
    assert ingested_at > FIXED_CONNECT_AT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest sim/tests/test_api_internal.py -v`
Expected: every test fails with a 404 (routes don't exist yet) or an `AttributeError`/collection error if Postgres isn't reachable locally — in that case start it first (`brew services start postgresql@16` and `createdb fantavo_test`, per `sim/tests/conftest.py`'s own skip message).

- [ ] **Step 3: Add the imports**

In `sim/api/app.py`, insert two new import lines between the existing `from sim.api.playoff_planner_view import (...)` block and `from sim.api.roast_view import (...)` block (alphabetical order, matching this file's existing isort-sorted import block):

```python
from sim.api.precompute import precompute_all_leagues
from sim.api.reingest import reingest_all_connected_users
```

- [ ] **Step 4: Add `require_cron_secret`**

In `sim/api/app.py`, insert immediately after `require_league_owner`'s closing `return user` line (right before `@app.get("/health")`):

```python
def require_cron_secret(authorization: str | None = Header(default=None)) -> None:
    """Gates the two /internal/* endpoints Vercel Cron Jobs call. Not a
    user-facing auth check (no AuthedUser involved) -- this exists so an
    arbitrary public request can't repeatedly trigger a 10,000-sim Monte
    Carlo precompute run. Verify against CRON_SECRET, an env var only
    Vercel's own Cron trigger and this deployment's own config know."""
    expected = os.environ.get("CRON_SECRET")
    if not expected or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid or missing cron secret")
```

- [ ] **Step 5: Add the two routes**

In `sim/api/app.py`, insert immediately after the `health` route handler's `return {"status": "ok"}` line (right before `@app.post("/auth/signup", ...)`):

```python
@app.post("/internal/precompute")
def trigger_precompute(
    _: None = Depends(require_cron_secret),  # noqa: B008 (idiomatic FastAPI)
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> dict[str, str]:
    """Vercel Cron Job target replacing sim/api/scheduler.py's in-process
    precompute interval job for a serverless deploy (see docs/decisions.md's
    Vercel Serverless Migration entry) -- calls the exact same function
    local development's in-process scheduler calls, once per invocation."""
    precompute_all_leagues(conn, datetime.now(UTC))
    return {"status": "ok"}


@app.post("/internal/reingest")
def trigger_reingest(
    _: None = Depends(require_cron_secret),  # noqa: B008 (idiomatic FastAPI)
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> dict[str, str]:
    """Vercel Cron Job target replacing sim/api/scheduler.py's in-process
    reingest interval job for a serverless deploy -- see trigger_precompute
    above for the same reasoning."""
    reingest_all_connected_users(conn, datetime.now(UTC))
    return {"status": "ok"}
```

- [ ] **Step 6: Document `CRON_SECRET` in `.env.example`**

Append to `.env.example`, following its existing per-var comment convention:

```
# CRON_SECRET authenticates Vercel Cron Job requests to the two
# /internal/* endpoints (sim/api/app.py's require_cron_secret) -- Vercel
# sets this itself when deployed and sends it back as
# `Authorization: Bearer <CRON_SECRET>` on every Cron-triggered request
# (see docs/decisions.md's Vercel Serverless Migration entry, and verify
# this exact header/scheme against Vercel's current docs before relying on
# it in production). Only relevant for a Vercel deployment -- unset
# locally, local dev never calls these routes (the in-process scheduler in
# sim/api/scheduler.py runs instead).
CRON_SECRET=
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest sim/tests/test_api_internal.py -v`
Expected: PASS (5 tests)

- [ ] **Step 8: Typecheck and lint**

Run: `mypy --strict sim` and `ruff check sim`
Expected: no new errors beyond this repo's existing pre-existing baseline (see `docs/decisions.md` Phase 18's verification note for what that baseline is).

- [ ] **Step 9: Commit**

```bash
git add sim/api/app.py sim/tests/test_api_internal.py .env.example
git commit -m "sim: add require_cron_secret and /internal/precompute, /internal/reingest routes"
```

---

## Task 2: Conditional `lifespan()` — gate the in-process scheduler behind `VERCEL`

**Files:**
- Modify: `sim/api/app.py` (the `_scheduler`/`lifespan` block, ~line 1115-1127)
- Test: `sim/tests/test_api_app.py` (append near the top, after imports)

**Interfaces:**
- Consumes: `os.environ` (already imported as `os` in `app.py`).
- Produces: `_should_run_in_process_scheduler() -> bool` — `True` unless `VERCEL=1` is set. `lifespan()`'s behavior when `_should_run_in_process_scheduler()` is `False`: it does not call `start_scheduler()`, but the `yield`/shutdown structure is otherwise identical.

- [ ] **Step 1: Write the failing tests**

Add to `sim/tests/test_api_app.py`, after the existing imports (before the `TEST_DSN` line):

```python
def test_should_run_in_process_scheduler_is_true_when_vercel_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VERCEL", raising=False)
    assert app_module._should_run_in_process_scheduler() is True


def test_should_run_in_process_scheduler_is_false_on_vercel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VERCEL", "1")
    assert app_module._should_run_in_process_scheduler() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest sim/tests/test_api_app.py -k should_run_in_process_scheduler -v`
Expected: FAIL with `AttributeError: module 'sim.api.app' has no attribute '_should_run_in_process_scheduler'`

- [ ] **Step 3: Add the helper and gate `lifespan()`**

In `sim/api/app.py`, replace:

```python
_scheduler: Any = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global _scheduler
    _scheduler = start_scheduler()
    try:
        yield
    finally:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            _scheduler = None
```

with:

```python
_scheduler: Any = None


def _should_run_in_process_scheduler() -> bool:
    """False on Vercel (VERCEL=1, set automatically by Vercel's runtime --
    verify this exact variable against Vercel's current docs at deploy
    time, see docs/decisions.md's Vercel Serverless Migration entry): a
    serverless function has no persistent process for an in-process
    APScheduler background thread to survive between invocations, and
    Vercel Cron Jobs calling /internal/precompute and /internal/reingest
    take over that role there instead. True everywhere else (local
    `uvicorn`, or a future non-Vercel host), preserving automatic
    recurring precompute/reingest for local development."""
    return os.environ.get("VERCEL") != "1"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global _scheduler
    if _should_run_in_process_scheduler():
        _scheduler = start_scheduler()
    try:
        yield
    finally:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            _scheduler = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest sim/tests/test_api_app.py -k should_run_in_process_scheduler -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full existing app test suite to confirm nothing regressed**

Run: `pytest sim/tests/test_api_app.py sim/tests/test_api_internal.py -v`
Expected: PASS, all tests (the existing `client` fixtures across the suite monkeypatch `start_scheduler` directly, so this change doesn't affect them — `VERCEL` is unset in the test environment, so `_should_run_in_process_scheduler()` returns `True` and `start_scheduler` still gets called, just now to the monkeypatched no-op).

- [ ] **Step 6: Typecheck and lint**

Run: `mypy --strict sim` and `ruff check sim`
Expected: no new errors.

- [ ] **Step 7: Commit**

```bash
git add sim/api/app.py sim/tests/test_api_app.py
git commit -m "sim: gate the in-process scheduler behind VERCEL, add _should_run_in_process_scheduler"
```

---

## Task 3: Vercel entry point and routing config

**Files:**
- Create: `api/index.py`
- Create: `vercel.json` (repo root)
- Test: `sim/tests/test_vercel_entrypoint.py` (new)

**Interfaces:**
- Consumes: `sim.api.app.app` (the existing FastAPI app object, unchanged).
- Produces: `api.index.app` — must be the identical object as `sim.api.app.app` (not a re-instantiated copy), since Task 4's `docs/decisions.md` entry and any future Vercel deploy depend on this being the real, fully-wired app (routes, dependencies, `lifespan`).

- [ ] **Step 1: Write the failing test**

Create `sim/tests/test_vercel_entrypoint.py`:

```python
"""Confirms api/index.py (the Vercel serverless entry point -- see
docs/decisions.md's Vercel Serverless Migration entry) actually exposes the
real FastAPI app object Vercel's Python builder needs, not a stale copy or
a re-instantiated app that would silently drop all routes, dependencies,
and the lifespan wiring."""

from __future__ import annotations

from api.index import app as vercel_app
from sim.api.app import app as real_app


def test_vercel_entrypoint_exposes_the_real_app_object() -> None:
    assert vercel_app is real_app
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest sim/tests/test_vercel_entrypoint.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api'`

- [ ] **Step 3: Create `api/index.py`**

```python
"""Vercel serverless entry point for the /sim FastAPI service. Vercel's
Python builder is documented to look for a WSGI/ASGI `app` object at
exactly this path (api/index.py, repo root -- matching where
requirements.txt/pyproject.toml already sit for the same build to install
from). See docs/decisions.md's Vercel Serverless Migration entry for the
full reasoning, and its "known gaps" note: this exact file path and
vercel.json's "functions" key shape are best-effort against Vercel's
documented conventions, not verified against a real deploy.

Deliberately a plain re-export, not a re-instantiated FastAPI() app --
every route, dependency, and the conditional lifespan() defined in
sim/api/app.py must be reused completely unmodified."""

from __future__ import annotations

from sim.api.app import app

__all__ = ["app"]
```

- [ ] **Step 4: Create `vercel.json`**

```json
{
  "functions": {
    "api/index.py": { "runtime": "python3.12" }
  },
  "rewrites": [{ "source": "/(.*)", "destination": "/api/index" }],
  "crons": [
    { "path": "/internal/precompute", "schedule": "0 */6 * * *" },
    { "path": "/internal/reingest", "schedule": "0 */6 * * *" }
  ]
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest sim/tests/test_vercel_entrypoint.py -v`
Expected: PASS

- [ ] **Step 6: Typecheck and lint the new root-level file**

Run: `mypy --strict api` and `ruff check api`
Expected: clean (these paths aren't in the repo's established `mypy --strict sim ingest` / `ruff check sim ingest db scripts` verification commands yet — this is new source at the repo root, so check it directly).

- [ ] **Step 7: Commit**

```bash
git add api/index.py vercel.json sim/tests/test_vercel_entrypoint.py
git commit -m "Add Vercel entry point (api/index.py) and vercel.json"
```

---

## Task 4: Remove `railway.toml`, record the pivot in `docs/decisions.md`, final verification

**Files:**
- Delete: `railway.toml`
- Modify: `docs/decisions.md` (append a new entry after Phase 18)

**Interfaces:**
- Consumes: nothing new — this task is documentation plus a deletion, and the full-suite verification run other tasks' work has already been individually tested against.
- Produces: nothing later tasks depend on (this is the last task).

- [ ] **Step 1: Remove `railway.toml`**

```bash
git rm railway.toml
```

- [ ] **Step 2: Run the full test suite and note the pass count**

Run: `pytest sim/tests ingest/tests -q`
Expected: all passing. Note the exact test count from the output — it's needed for Step 4 below (this repo's `docs/decisions.md` entries always cite the real count from an actual run, e.g. "320 tests" in Phase 18's entry).

- [ ] **Step 3: Typecheck and lint everything touched**

Run: `mypy --strict sim ingest api` and `ruff check sim ingest db scripts api`
Expected: no new errors beyond the same pre-existing baseline documented in Phase 18's `docs/decisions.md` entry (confirm identical via `git stash` if anything unexpected shows up, same method that entry used).

Also run, to confirm `/web` wasn't affected (no `/web` files changed in this plan):
Run (from `/web`): `npx tsc --noEmit && npx eslint .`
Expected: clean.

- [ ] **Step 4: Append the `docs/decisions.md` entry**

Add after Phase 18's final line (the file's current last line), using the exact test count from Step 2:

```markdown

## Phase 19 — Vercel Serverless Migration (supersedes Railway)

- **`/sim` now deploys to Vercel, alongside `/web`, reversing the previous
  session's Railway decision (`e747df1`) -- not undoing that work.** The
  health endpoint, production `/docs`/`/redoc`/`/openapi.json` gating, and
  the `pyproject.toml`/`requirements.txt` dependency manifest all carry
  over unchanged; they were always host-agnostic. Only `railway.toml`
  (removed) and the in-process-scheduler assumption it depended on are
  superseded.
- **Why the reversal:** Vercel's core model is serverless functions with
  no persistent process between requests. `sim/api/scheduler.py`'s
  in-process APScheduler `BackgroundScheduler` -- the reason `/sim` was
  routed to Railway in the first place -- cannot survive that model
  unmodified. This phase does the actual rework (Vercel Cron Jobs instead
  of an in-process scheduler) rather than keeping a second hosting
  platform around just to dodge it.
- **Two new stateless endpoints, `POST /internal/precompute` and
  `POST /internal/reingest` (`sim/api/app.py`), replace the scheduler's
  two interval jobs as Cron Jobs' fire targets.** Both call the exact same
  `precompute_all_leagues`/`reingest_all_connected_users` functions the
  old scheduler called -- one pass per invocation, matching what a Cron
  trigger needs (fire once, run to completion, return). Neither function
  itself changed.
- **`require_cron_secret` gates both new endpoints** -- not a user-facing
  auth check (no `AuthedUser`), it exists so an arbitrary public request
  can't repeatedly trigger a 10,000-sim Monte Carlo precompute run.
  Verifies `Authorization: Bearer <CRON_SECRET>` against a `CRON_SECRET`
  env var only Vercel's own Cron trigger and this deployment's config
  know -- Vercel's documented convention for authenticating its own Cron
  requests.
- **`lifespan()` is now conditional, not removed.** `sim/api/scheduler.py`
  is unchanged and still the right behavior for local development, where
  a developer runs a persistent `uvicorn` process and benefits from
  automatic recurring precompute/reingest with no local cron setup.
  `_should_run_in_process_scheduler()` (`sim/api/app.py`) checks Vercel's
  own auto-set `VERCEL=1` environment variable; `lifespan()` only starts
  `start_scheduler()` when that helper returns `True`. Extracted as a
  standalone function specifically so it has a direct unit test rather
  than requiring the async context manager itself to be exercised.
- **`api/index.py` (new, repo root) is the Vercel entry point**: a
  4-line re-export of `sim.api.app.app`, at the exact path
  (`api/index.py`) Vercel's Python builder is documented to look for.
  Every existing route, dependency, and auth check is reused completely
  unmodified -- this is deployment mechanics, not a route-level rewrite.
- **`vercel.json` (new, repo root)** wraps `/sim`'s own Vercel project: a
  `functions` entry pointing `api/index.py` at the `python3.12` runtime, a
  catch-all rewrite sending every path to that one function, and two Cron
  Job entries (`/internal/precompute`, `/internal/reingest`) on the same
  `0 */6 * * *` cadence `PRECOMPUTE_INTERVAL_HOURS`/`REINGEST_INTERVAL_HOURS`
  already used.
- **Postgres: Vercel Postgres (Neon), via its pooled connection string --
  no code change.** `get_connection` (`sim/api/app.py`) already opens one
  `psycopg.connect(dsn)` per request via a FastAPI dependency, exactly the
  shape Neon's pooler is designed for ("many short-lived connections").
  `DATABASE_URL` just needs to point at the pooled endpoint once that
  database exists.
- **An explicitly-flagged, accepted risk, not resolved by this phase:**
  Vercel's exact Python/FastAPI serverless mechanics -- the `vercel.json`
  `functions`/`runtime` key shape, the `CRON_SECRET` header/scheme, and
  the `VERCEL=1` auto-set env var -- are implemented at best-effort
  confidence against Vercel's documented ASGI-function pattern, not
  verified against a real deploy (this project has no Vercel account/CLI
  set up yet, confirmed before this phase started -- no `vercel` binary,
  no `.vercel/` directory, no existing `web/vercel.json`). The actual
  account setup, Vercel Postgres/Neon provisioning, the two Vercel
  projects, and running the real deploy remain manual, user-driven steps,
  same as every other deployment-account action in this project --
  closing this gap needs a real deploy, which is exactly what will
  surface any correction needed.
- **Verification**: `pytest sim/tests ingest/tests -q` all passing (<N>
  tests, including the new `sim/tests/test_api_internal.py` and
  `sim/tests/test_vercel_entrypoint.py`); `mypy --strict sim ingest api`
  and `ruff check sim ingest db scripts api` clean; `npx tsc --noEmit` and
  `npx eslint .` clean in `/web` (no `/web` changes in this phase,
  re-verified anyway). No live Vercel deploy was performed or is claimed
  here -- see the risk note above.
```

Replace `<N>` with the real count from Step 2.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Remove railway.toml, record Vercel Serverless Migration in docs/decisions.md"
```

---

## Self-Review Notes (for the plan author, not a task)

- **Spec coverage:** Decision 1 (two Vercel projects) — no `/sim`-side code needed, `/web` untouched, covered by Global Constraints and the decisions.md entry. Decision 2 (single serverless function) — Task 3. Decision 3 (Neon pooled connection, no code change) — explicitly called out as needing nothing, documented in Task 4's decisions.md entry. Decision 4 (Cron Jobs replace APScheduler) — Tasks 1 and 2. Decision 5 (defer pooler tuning) — Global Constraints note plus decisions.md's risk paragraph. Scope's file list (new: `api/index.py`, `vercel.json`; modified: `sim/api/app.py`, `docs/decisions.md`; removed: `railway.toml`) — all four covered across Tasks 1-4. Testing section's three bullets (`require_cron_secret` 401/401/pass, endpoint DB-observable side effects, extracted `_should_run_in_process_scheduler()` helper) — Tasks 1 and 2 exactly. Verification section — Task 4, Step 3, plus each task's own typecheck/lint step.
- **Deliberately not a task:** the design's own "plan's first task is a minimal hello-world FastAPI deploy" — this repo has no Vercel account/CLI configured (verified directly: no `vercel` binary, no `.vercel/`, no `web/vercel.json`), so an actual deploy is a manual, user-driven action per the design's own Out-of-scope section, not something this plan can execute. `api/index.py` is built directly against the real 20-route app (per the design's own "File structure" list, which names no separate scratch/hello-world file) with a local identity-check test (Task 3) standing in for what local tooling can verify; the real platform-mechanics verification happens when the user actually deploys.
