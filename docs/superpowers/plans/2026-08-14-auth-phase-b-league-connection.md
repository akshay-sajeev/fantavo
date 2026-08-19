# Auth Phase B (League Connection) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a signed-in user connect their real ESPN league (public or private, with their own credentials), pick which team is theirs, and see that real league everywhere `DEFAULT_LEAGUE_ID` used to be — kept current by a recurring background re-fetch.

**Architecture:** The only new live-ESPN-calling code is `ingest/espn_client.py`, with an injectable transport so nothing in the test suite ever makes a real network call. A new `sim/api/league_connection_view.py` owns validating a connect attempt (one live fetch), persisting encrypted credentials, calling the *existing, unchanged* `ingest_league()`, and reporting connection state — all behind Phase A's `require_user()`. A second `BackgroundScheduler` job (`sim/api/reingest.py`, alongside the existing `precompute_all_leagues`) re-fetches every connected user's league every 6 hours. The web layer gains a two-step `/connect-league` → `/connect-league/pick-team` flow and loses `DEFAULT_LEAGUE_ID` entirely.

**Tech Stack:** FastAPI, psycopg3, `cryptography` (Fernet, credential encryption), `requests` (now a declared production dependency, not just a dev-script one), Postgres, APScheduler. Next.js 15 Route Handlers, `next/headers` cookies, React `cache()`.

## Global Constraints

These apply to every task below; not repeated per-task.

- Every timestamp passed to a DB write or an ESPN season resolution is an explicit caller-supplied `datetime`, never a server-side `now()` default — same rule Phase A followed.
- `espn_s2`, `SWID`, and `CREDENTIAL_ENCRYPTION_KEY` are never logged, never placed in an exception message, never written to a fixture, and never hardcoded in a test alongside a value that also appears verbatim in the database — the same rule Phase A applied to passwords and session tokens, extended to these new secrets.
- No test in this repo ever makes a real HTTP call to ESPN. `ingest/espn_client.py`'s `fetch_live_league` takes an injectable `transport`; every test that needs it monkeypatches the function (or passes a fake transport) rather than hitting the network.
- All new Python code passes `mypy --strict sim ingest` with zero new errors (21 pre-existing, unrelated errors remain in `sim/engine.py`/`sim/tests/test_engine.py` — do not touch those) and `ruff check sim ingest` clean.
- All new TypeScript passes `npx tsc --noEmit` and `npx eslint .` clean from `/web`.
- Every DB write is wrapped in `conn.transaction()` (never a bare `conn.execute()` outside one), matching `sim/api/auth_view.py`'s established pattern.
- `POST /leagues/connect` failure messages *may* be specific (bad league id vs. bad cookies vs. private-league-missing-cookies) — unlike Phase A's login/signup, there is no other account's existence to protect here.

---

## File Structure

**Backend (new):**
- `db/migrations/0004_league_connection.sql` — extends `app_user` with league/team/credential columns.
- `sim/api/crypto.py` — `encrypt_credential`/`decrypt_credential` (Fernet, server-held master key).
- `ingest/espn_client.py` — `fetch_live_league`, the first live-capable ESPN HTTP client.
- `sim/api/league_connection_view.py` — `connect_league`, `set_team`, `get_connection_state`, `list_teams_for_league`.
- `sim/api/reingest.py` — `reingest_user`, `reingest_all_connected_users` (mirrors `sim/api/precompute.py`).
- `sim/tests/test_crypto.py`, `ingest/tests/test_espn_client.py`, `sim/tests/test_api_league_connection.py`, `sim/tests/test_reingest.py`.

**Backend (modified):**
- `pyproject.toml` — add `cryptography` and `requests` as real dependencies; add a `requests.*` mypy override (mirrors the existing `apscheduler.*`/`scipy.*` overrides).
- `.env.example` — add `CREDENTIAL_ENCRYPTION_KEY`.
- `sim/api/app.py` — import `league_connection_view`, add 4 Pydantic models, add 3 routes.
- `sim/api/scheduler.py` — add the re-ingest job alongside the existing precompute job.
- `scripts/fetch_fixture.py` — its `fetch()` function is replaced by a call to `ingest.espn_client.fetch_live_league`; its own scrub-and-save-to-`/fixtures` behavior is unchanged.

**Frontend (new):**
- `web/lib/leagueConnection.ts` — `getLeagueConnection()`, mirrors `web/lib/auth.ts`.
- `web/app/api/leagues/connect/route.ts`, `web/app/api/leagues/team/route.ts`.
- `web/app/connect-league/page.tsx`, `web/components/connect-league/connect-league-form.tsx`.
- `web/app/connect-league/pick-team/page.tsx`, `web/components/connect-league/team-picker-form.tsx`.

**Frontend (modified):**
- `web/lib/types.ts` — add `TeamOption`, `ConnectLeagueResponse`, `LeagueConnection`.
- `web/lib/api.ts` — extend `authedFetch` to accept an optional JSON body for `POST`; add `postLeaguesConnect`, `postLeaguesTeam`, `getLeaguesMe`.
- `web/app/page.tsx` — replaces the unconditional `DEFAULT_LEAGUE_ID` redirect with connection-state-aware routing.

---

### Task 1: League connection schema migration

**Files:**
- Create: `db/migrations/0004_league_connection.sql`
- Test: none (schema-only; verified by Task 4's tests writing to these columns)

**Interfaces:**
- Produces: `app_user` gains `espn_league_id`, `espn_season_id`, `espn_team_id`, `espn_s2_encrypted`, `espn_swid_encrypted`, `league_connected_at` (all nullable).

- [ ] **Step 1: Write the migration**

```sql
-- Auth Phase B: connects a signed-in user to their real ESPN league.
-- Extends app_user (Phase A) rather than a separate join table -- this
-- phase is fixed at one league per user (see
-- docs/superpowers/specs/2026-08-14-auth-phase-b-league-connection-design.md,
-- Decision 2), so a 1:1 set of nullable columns is simpler than a table
-- whose only purpose would be a 1:1 relation. All six columns are NULL
-- together for a freshly signed-up user; espn_team_id specifically stays
-- NULL between "league connected" and "team picked" -- the two-step part
-- of the connect flow.
ALTER TABLE app_user
    ADD COLUMN espn_league_id      BIGINT,
    ADD COLUMN espn_season_id      INTEGER,
    ADD COLUMN espn_team_id        INTEGER,
    -- Fernet ciphertext (opaque bytes -- sim.api.crypto). NULL together for
    -- a public league, which needs no cookies at all.
    ADD COLUMN espn_s2_encrypted   BYTEA,
    ADD COLUMN espn_swid_encrypted BYTEA,
    ADD COLUMN league_connected_at TIMESTAMPTZ;
```

- [ ] **Step 2: Apply it and confirm the columns exist**

Run:
```bash
python3 -c "
from ingest.db import connect, run_migrations, DEFAULT_DEV_DSN
conn = connect(DEFAULT_DEV_DSN)
run_migrations(conn)
conn.commit()
with conn.cursor() as cur:
    cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name = 'app_user' ORDER BY column_name\")
    print(cur.fetchall())
conn.close()
"
```
Expected: the output includes `espn_league_id`, `espn_s2_encrypted`, `espn_season_id`, `espn_swid_encrypted`, `espn_team_id`, `league_connected_at` alongside the existing `email`/`email_norm`/`password_hash`/`created_at`/`user_id` columns.

- [ ] **Step 3: Commit**

```bash
git add db/migrations/0004_league_connection.sql
git commit -m "Add league connection columns to app_user"
```

---

### Task 2: Dependencies + credential encryption

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Create: `sim/api/crypto.py`
- Test: `sim/tests/test_crypto.py`

**Interfaces:**
- Produces:
  - `CredentialEncryptionError(ValueError)`
  - `encrypt_credential(plaintext: str) -> bytes`
  - `decrypt_credential(ciphertext: bytes) -> str`

- [ ] **Step 1: Add the dependencies**

In `pyproject.toml`, find:
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
    # Fernet symmetric encryption for per-user ESPN credentials at rest
    # (Auth Phase B) -- see sim/api/crypto.py.
    "cryptography>=42.0.0",
    # ESPN's live fantasy API client (Auth Phase B: ingest/espn_client.py).
    # requests was already used ad hoc by scripts/fetch_fixture.py (a
    # pre-Phase-13 dev-only script); this phase is what makes it a real,
    # load-bearing production dependency, so it gets a real entry now.
    "requests>=2.31.0",
]
```

Add this override alongside the existing `apscheduler.*`/`scipy.*` ones (find the `[[tool.mypy.overrides]]` blocks and add a third):
```toml
# requests ships no inline type stubs and the separate types-requests
# package isn't installed -- ingest/espn_client.py calls only
# requests.Session.get/.get, not worth vendoring stubs for (same reasoning
# as the apscheduler override above).
[[tool.mypy.overrides]]
module = "requests.*"
ignore_missing_imports = true
```

- [ ] **Step 2: Install the new dependencies**

```bash
python3 -m pip install cryptography requests
```

- [ ] **Step 3: Document the new secret in `.env.example`**

Find:
```
GEMINI_API_KEY=
```
Append after it:
```
# CREDENTIAL_ENCRYPTION_KEY encrypts each connected user's ESPN espn_s2/SWID
# cookies at rest (Auth Phase B) -- treat it like a master password: anyone
# with this key can decrypt every stored credential. Never commit .env or
# paste a real value here. Generate one with:
#   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
CREDENTIAL_ENCRYPTION_KEY=
```

- [ ] **Step 4: Write the failing tests**

Create `sim/tests/test_crypto.py`:

```python
"""Tests for sim.api.crypto -- reversible encryption for per-user ESPN
credentials at rest (Auth Phase B). Unlike auth_view's argon2id password
hashing, this must be reversible: the recurring re-ingest job
(sim/api/reingest.py) decrypts stored credentials with nobody present."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from sim.api.crypto import CredentialEncryptionError, decrypt_credential, encrypt_credential


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_encrypt_then_decrypt_roundtrips_to_the_original_value() -> None:
    plaintext = "a-real-espn-s2-cookie-value"
    ciphertext = encrypt_credential(plaintext)
    assert decrypt_credential(ciphertext) == plaintext


def test_encrypt_credential_output_does_not_contain_the_plaintext() -> None:
    plaintext = "a-very-specific-secret-cookie-xyz"
    ciphertext = encrypt_credential(plaintext)
    assert plaintext.encode("utf-8") not in ciphertext


def test_decrypt_credential_rejects_tampered_ciphertext() -> None:
    ciphertext = encrypt_credential("some-value")
    tampered = ciphertext[:-1] + (b"\x00" if ciphertext[-1:] != b"\x00" else b"\x01")
    with pytest.raises(CredentialEncryptionError):
        decrypt_credential(tampered)


def test_missing_key_raises_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
    with pytest.raises(CredentialEncryptionError, match="CREDENTIAL_ENCRYPTION_KEY"):
        encrypt_credential("anything")
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `pytest sim/tests/test_crypto.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sim.api.crypto'`

- [ ] **Step 6: Write `sim/api/crypto.py`**

```python
"""Symmetric encryption for per-user ESPN credentials at rest (Auth Phase B).

Reversible, unlike auth_view's argon2id password hashing -- the recurring
re-ingest job (sim/api/reingest.py) must decrypt a user's stored espn_s2/
SWID with nobody present, so a one-way hash cannot be used here. Fernet
(AES-128-CBC + HMAC, from the `cryptography` package) is authenticated
(tampered ciphertext fails to decrypt rather than silently returning
garbage), and there is no need for asymmetric encryption -- only this same
server process ever decrypts what it encrypts.

CREDENTIAL_ENCRYPTION_KEY is read from `.env` via sim.api.env's loader (same
convention as GEMINI_API_KEY -- see sim.api.analyst_view._get_client for the
identical lazy/memoized pattern this mirrors), never stored in the
database, never logged.
"""

from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from sim.api.env import load_dotenv_once


class CredentialEncryptionError(ValueError):
    """CREDENTIAL_ENCRYPTION_KEY is missing/malformed, or a ciphertext
    failed to decrypt (wrong key, or corrupted/tampered data). Never
    includes the key or the credential itself in its message."""


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """Lazy, memoized -- constructed on first real use, not at import time,
    so importing this module never requires CREDENTIAL_ENCRYPTION_KEY to be
    set (tests set it via the _fake_key autouse fixture instead)."""
    load_dotenv_once()
    key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not key:
        raise CredentialEncryptionError(
            "CREDENTIAL_ENCRYPTION_KEY is not set -- copy .env.example to .env "
            "and fill it in. (This message never includes the key itself.)"
        )
    try:
        return Fernet(key.encode("utf-8"))
    except ValueError as exc:
        raise CredentialEncryptionError(
            "CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key"
        ) from exc


def encrypt_credential(plaintext: str) -> bytes:
    return _get_fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_credential(ciphertext: bytes) -> str:
    try:
        return _get_fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialEncryptionError(
            "stored credential could not be decrypted -- wrong key or corrupted data"
        ) from exc
```

Note: `_get_fernet` is `@lru_cache`d, so `test_missing_key_raises_a_clear_error` must run before any other test in the same process populates the cache with a real key — since pytest runs test functions in file order by default and this is the last test in the file, this is safe as written. If tests are ever reordered, call `_get_fernet.cache_clear()` in the fixture.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest sim/tests/test_crypto.py -v`
Expected: 4 passed

- [ ] **Step 8: Typecheck and lint**

Run: `mypy --strict sim/api/crypto.py sim/tests/test_crypto.py && ruff check sim/api/crypto.py sim/tests/test_crypto.py`
Expected: no errors

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml .env.example sim/api/crypto.py sim/tests/test_crypto.py
git commit -m "Add credential encryption (Fernet) and cryptography/requests dependencies"
```

---

### Task 3: Live ESPN client (`ingest/espn_client.py`)

**Files:**
- Create: `ingest/espn_client.py`
- Test: `ingest/tests/test_espn_client.py`
- Modify: `scripts/fetch_fixture.py`

**Interfaces:**
- Produces:
  - `EspnFetchError(RuntimeError)`, `EspnAuthenticationError(EspnFetchError)`, `EspnLeagueNotFoundError(EspnFetchError)`
  - `fetch_live_league(league_id: int, season_id: int, espn_s2: str | None, swid: str | None, transport: Transport | None = None) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests**

Create `ingest/tests/test_espn_client.py`:

```python
"""Tests for ingest.espn_client.fetch_live_league -- the only module in
this repo that calls the real ESPN API from production code. Every test
here uses a fake Transport; none makes a real network call (CLAUDE.md's
"fixtures, not live calls" rule)."""

from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from ingest.espn_client import (
    EspnAuthenticationError,
    EspnFetchError,
    EspnLeagueNotFoundError,
    fetch_live_league,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeTransport:
    """Records every call and returns a canned response keyed on whether
    this is the free-agent view request or the main league request --
    fetch_live_league makes exactly one of each."""

    def __init__(self, league_status: int = 200, league_payload: Any = None) -> None:
        self.league_status = league_status
        self.league_payload = league_payload if league_payload is not None else {"teams": []}
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        params = kwargs.get("params") or []
        is_free_agent_call = ("view", "kona_player_info") in params
        if is_free_agent_call:
            return _FakeResponse(200, {"players": [{"id": 1, "fullName": "Fake Player"}]})
        return _FakeResponse(self.league_status, self.league_payload)


def test_fetch_live_league_merges_league_and_free_agent_responses() -> None:
    transport = _FakeTransport(league_payload={"id": 999, "teams": [{"id": 1, "name": "A"}]})
    result = fetch_live_league(999, 2026, None, None, transport=transport)
    assert result["id"] == 999
    assert result["_freeAgents"] == [{"id": 1, "fullName": "Fake Player"}]


def test_fetch_live_league_sends_cookies_only_when_both_are_provided() -> None:
    transport = _FakeTransport()
    fetch_live_league(999, 2026, "s2-value", "swid-value", transport=transport)
    assert transport.calls[0]["cookies"] == {"espn_s2": "s2-value", "SWID": "swid-value"}


def test_fetch_live_league_sends_no_cookies_for_a_public_league() -> None:
    transport = _FakeTransport()
    fetch_live_league(999, 2026, None, None, transport=transport)
    assert transport.calls[0]["cookies"] == {}


def test_fetch_live_league_raises_authentication_error_on_401() -> None:
    transport = _FakeTransport(league_status=401)
    with pytest.raises(EspnAuthenticationError):
        fetch_live_league(999, 2026, "wrong", "wrong", transport=transport)


def test_fetch_live_league_raises_not_found_error_on_404() -> None:
    transport = _FakeTransport(league_status=404)
    with pytest.raises(EspnLeagueNotFoundError):
        fetch_live_league(123456789, 2026, None, None, transport=transport)


def test_fetch_live_league_raises_generic_error_on_other_failures() -> None:
    transport = _FakeTransport(league_status=500)
    with pytest.raises(EspnFetchError):
        fetch_live_league(999, 2026, None, None, transport=transport)


def test_fetch_live_league_wraps_network_errors() -> None:
    class _RaisingTransport:
        def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            raise requests.ConnectionError("no route to host")

    with pytest.raises(EspnFetchError):
        fetch_live_league(999, 2026, None, None, transport=_RaisingTransport())


def test_fetch_live_league_survives_a_failed_free_agent_call() -> None:
    class _PartialFailureTransport:
        def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            params = kwargs.get("params") or []
            if ("view", "kona_player_info") in params:
                return _FakeResponse(500, {})
            return _FakeResponse(200, {"id": 999, "teams": []})

    result = fetch_live_league(999, 2026, None, None, transport=_PartialFailureTransport())
    assert result["_freeAgents"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest ingest/tests/test_espn_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ingest.espn_client'`

- [ ] **Step 3: Write `ingest/espn_client.py`**

```python
"""Live ESPN fantasy API client -- the only module allowed to call the real
ESPN API from the always-running service (Auth Phase B's connect flow and
recurring re-ingest job). scripts/fetch_fixture.py (dev-only, manual) also
calls this module rather than duplicating the fetch/merge logic, and layers
its own scrub-and-write-to-/fixtures behavior on top.

CLAUDE.md's "fixtures, not live calls" rule still governs every test in
this repo: `fetch_live_league`'s `transport` parameter is injectable
specifically so tests substitute a fake and never make a real network call.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

import requests

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

VIEWS = [
    "mTeam",
    "mRoster",
    "mMatchup",
    "mMatchupScore",
    "mSettings",
    "mStandings",
    "mDraftDetail",
    "mStatus",
]

FREE_AGENT_FILTER = {
    "players": {
        "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        "limit": 300,
    }
}


class Transport(Protocol):
    def get(self, url: str, **kwargs: Any) -> requests.Response: ...


class EspnFetchError(RuntimeError):
    """Base for every error this module raises. Never includes espn_s2/SWID
    in its message, matching CLAUDE.md's secrets rule."""


class EspnAuthenticationError(EspnFetchError):
    """ESPN returned 401 -- missing, wrong, or expired espn_s2/SWID cookies
    for a private league."""


class EspnLeagueNotFoundError(EspnFetchError):
    """ESPN returned 404 -- no league exists with this league_id/season_id."""


def fetch_live_league(
    league_id: int,
    season_id: int,
    espn_s2: str | None,
    swid: str | None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Fetch and merge every view in VIEWS plus the free-agent pool into the
    single combined payload shape ingest.db.ingest_league expects -- the
    same shape scripts/fetch_fixture.py has always produced.

    `transport` defaults to a real requests.Session(); tests pass a fake
    implementing .get(url, **kwargs) so no network call is ever made in
    this repo's test suite. A failed free-agent-pool call is non-fatal
    (matches scripts/fetch_fixture.py's existing behavior) -- it degrades
    to an empty pool rather than failing the whole connect/re-ingest
    attempt over a secondary endpoint.
    """
    session: Transport = transport if transport is not None else requests.Session()
    cookies = {"espn_s2": espn_s2, "SWID": swid} if espn_s2 and swid else {}

    url = f"{BASE}/seasons/{season_id}/segments/0/leagues/{league_id}"
    params = [("view", v) for v in VIEWS]

    try:
        response = session.get(url, params=params, cookies=cookies, timeout=30)
    except requests.RequestException as exc:
        raise EspnFetchError(f"could not reach ESPN: {type(exc).__name__}") from exc

    if response.status_code == 401:
        raise EspnAuthenticationError(
            "ESPN rejected the request -- for a private league, espn_s2/SWID "
            "must be valid and not expired"
        )
    if response.status_code == 404:
        raise EspnLeagueNotFoundError(
            f"no ESPN league found for league_id={league_id} season_id={season_id}"
        )
    if not response.ok:
        raise EspnFetchError(f"ESPN returned HTTP {response.status_code}")

    league: dict[str, Any] = response.json()

    fa_response = session.get(
        url,
        params=[("view", "kona_player_info")],
        cookies=cookies,
        headers={"X-Fantasy-Filter": json.dumps(FREE_AGENT_FILTER)},
        timeout=30,
    )
    league["_freeAgents"] = fa_response.json().get("players", []) if fa_response.ok else []

    return league
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest ingest/tests/test_espn_client.py -v`
Expected: 8 passed

- [ ] **Step 5: Refactor `scripts/fetch_fixture.py` to use the new client**

In `scripts/fetch_fixture.py`, remove the module-level `BASE`, `VIEWS`, `FREE_AGENT_FILTER` constants and the `fetch()` function (they now live in `ingest/espn_client.py`). Replace the `import requests` block's usage and add:

```python
from ingest.espn_client import EspnAuthenticationError, EspnFetchError, fetch_live_league
```

In `main()`, find:
```python
    print(f"Fetching league {env['league_id']}, season {args.season}...")
    raw = fetch(args.season, env["league_id"], cookies)
```
Replace with:
```python
    print(f"Fetching league {env['league_id']}, season {args.season}...")
    try:
        raw = fetch_live_league(
            int(env["league_id"]), args.season, env["espn_s2"] or None, env["swid"] or None
        )
    except EspnAuthenticationError:
        raise SystemExit(
            "401 from ESPN. For a private league you need valid ESPN_S2 and SWID "
            "cookies; they expire, so re-copy them from your browser."
        ) from None
    except EspnFetchError as exc:
        raise SystemExit(f"Fetch failed: {exc}") from None
```

At the bottom of the file, the existing `except requests.RequestException` handler in the `if __name__ == "__main__":` block can stay as a defensive fallback but is no longer the primary error path (network errors are now caught inside `fetch_live_league` and re-raised as `EspnFetchError`, handled above); leave it as-is, it is harmless dead code that costs nothing to keep and matches this refactor's "targeted deduplication, not a rewrite" scope from the design doc.

- [ ] **Step 6: Manually confirm the script still imports cleanly**

Run: `python3 -c "import scripts.fetch_fixture"`
Expected: no output, no error (confirms no syntax/import errors from the edit; this script's actual `main()` still requires real `.env` credentials to run end-to-end, which is out of scope for automated verification here — see this plan's final task).

- [ ] **Step 7: Typecheck and lint**

Run: `mypy --strict ingest/espn_client.py ingest/tests/test_espn_client.py && ruff check ingest/espn_client.py ingest/tests/test_espn_client.py scripts/fetch_fixture.py`
Expected: no errors

- [ ] **Step 8: Run the full existing suite to confirm nothing broke**

Run: `pytest sim/tests ingest/tests -q`
Expected: all previously-passing tests still pass, plus the 8 new ones

- [ ] **Step 9: Commit**

```bash
git add ingest/espn_client.py ingest/tests/test_espn_client.py scripts/fetch_fixture.py
git commit -m "Add live ESPN client (ingest/espn_client.py), refactor fetch_fixture.py to use it"
```

---

### Task 4: League connection business logic

**Files:**
- Create: `sim/api/league_connection_view.py`
- Test: `sim/tests/test_api_league_connection.py`

**Interfaces:**
- Consumes: `fetch_live_league`, `EspnFetchError` (Task 3); `encrypt_credential`, `decrypt_credential` (Task 2); `ingest_league` (`ingest/db.py`, unchanged); `AuthedUser`/`create_user` (Phase A, for tests only).
- Produces:
  - `TeamOption(team_id: int, name: str)` (frozen dataclass)
  - `ConnectionState(league_id: int | None, season_id: int | None, team_id: int | None, connected_at: datetime | None)` (frozen dataclass)
  - `LeagueConnectionError(ValueError)`, `UnknownTeamError(ValueError)`, `NoConnectedLeagueError(ValueError)`
  - `resolve_current_season_id(now: datetime) -> int`
  - `connect_league(conn, user_id: int, league_id: int, espn_s2: str | None, swid: str | None, now: datetime) -> tuple[TeamOption, ...]`
  - `set_team(conn, user_id: int, team_id: int) -> None`
  - `get_connection_state(conn, user_id: int) -> ConnectionState`
  - `list_teams_for_league(conn, league_id: int, season_id: int) -> tuple[TeamOption, ...]`

- [ ] **Step 1: Write the failing tests**

Create `sim/tests/test_api_league_connection.py`. This uses the `pg_conn` and `raw_fixture` fixtures already defined in `sim/tests/conftest.py` (the same real, committed fixture `ingest.db.ingest_league` is tested against everywhere else) — `raw_fixture["id"]` is `885686492`, `raw_fixture["teams"]` are the real teams, and `resolve_current_season_id` on the `FIXED_NOW` below resolves to `2026`, matching the fixture's real `seasonId`.

```python
"""Tests for sim.api.league_connection_view (Auth Phase B).

No real ESPN calls -- ingest.espn_client.fetch_live_league is monkeypatched
per-test to return sim/tests/conftest.py's raw_fixture, so these tests
exercise the real ingest_league() path end-to-end without a network call.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest

from ingest.espn_client import EspnAuthenticationError
from sim.api import auth_view, league_connection_view
from sim.api.league_connection_view import (
    LeagueConnectionError,
    NoConnectedLeagueError,
    UnknownTeamError,
    connect_league,
    get_connection_state,
    list_teams_for_league,
    resolve_current_season_id,
    set_team,
)

FIXED_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _make_user(pg_conn: psycopg.Connection[Any]) -> int:
    user = auth_view.create_user(pg_conn, "connector@example.com", "a-real-password", FIXED_NOW)
    return user.user_id


def test_resolve_current_season_id_uses_the_calendar_year() -> None:
    assert resolve_current_season_id(FIXED_NOW) == 2026


def test_connect_league_ingests_and_returns_teams(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    user_id = _make_user(pg_conn)

    teams = connect_league(pg_conn, user_id, raw_fixture["id"], None, None, FIXED_NOW)

    assert {t.team_id for t in teams} == {t["id"] for t in raw_fixture["teams"]}

    state = get_connection_state(pg_conn, user_id)
    assert state.league_id == raw_fixture["id"]
    assert state.season_id == 2026
    assert state.team_id is None
    assert state.connected_at == FIXED_NOW


def test_connect_league_encrypts_credentials_never_stores_plaintext(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    user_id = _make_user(pg_conn)
    espn_s2 = "a-very-specific-fake-cookie-value"
    swid = "{FAKE-SWID-0000-0000-000000000000}"

    connect_league(pg_conn, user_id, raw_fixture["id"], espn_s2, swid, FIXED_NOW)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT espn_s2_encrypted, espn_swid_encrypted FROM app_user WHERE user_id = %s",
            (user_id,),
        )
        encrypted_s2, encrypted_swid = cur.fetchone()
    assert espn_s2.encode("utf-8") not in encrypted_s2
    assert swid.encode("utf-8") not in encrypted_swid


def test_connect_league_public_league_stores_no_credentials(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    user_id = _make_user(pg_conn)

    connect_league(pg_conn, user_id, raw_fixture["id"], None, None, FIXED_NOW)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT espn_s2_encrypted, espn_swid_encrypted FROM app_user WHERE user_id = %s",
            (user_id,),
        )
        encrypted_s2, encrypted_swid = cur.fetchone()
    assert encrypted_s2 is None
    assert encrypted_swid is None


def test_connect_league_persists_nothing_on_espn_failure(
    pg_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise EspnAuthenticationError("bad cookies")

    monkeypatch.setattr(league_connection_view, "fetch_live_league", _raise)
    user_id = _make_user(pg_conn)

    with pytest.raises(LeagueConnectionError):
        connect_league(pg_conn, user_id, 12345, "wrong", "wrong", FIXED_NOW)

    state = get_connection_state(pg_conn, user_id)
    assert state.league_id is None


def test_set_team_accepts_a_real_team_and_rejects_a_fake_one(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    user_id = _make_user(pg_conn)
    connect_league(pg_conn, user_id, raw_fixture["id"], None, None, FIXED_NOW)
    real_team_id = raw_fixture["teams"][0]["id"]

    set_team(pg_conn, user_id, real_team_id)
    assert get_connection_state(pg_conn, user_id).team_id == real_team_id

    with pytest.raises(UnknownTeamError):
        set_team(pg_conn, user_id, 999999)


def test_set_team_without_a_connected_league_raises(pg_conn: psycopg.Connection[Any]) -> None:
    user_id = _make_user(pg_conn)
    with pytest.raises(NoConnectedLeagueError):
        set_team(pg_conn, user_id, 1)


def test_list_teams_for_league_reads_from_the_team_table(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    user_id = _make_user(pg_conn)
    connect_league(pg_conn, user_id, raw_fixture["id"], None, None, FIXED_NOW)

    teams = list_teams_for_league(pg_conn, raw_fixture["id"], 2026)
    assert {t.team_id for t in teams} == {t["id"] for t in raw_fixture["teams"]}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest sim/tests/test_api_league_connection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sim.api.league_connection_view'`

- [ ] **Step 3: Write `sim/api/league_connection_view.py`**

```python
"""Connecting a signed-in user's real ESPN league (Auth Phase B) -- see
docs/superpowers/specs/2026-08-14-auth-phase-b-league-connection-design.md.

No HTTP here (sim/api/app.py's 3 new routes), no direct ESPN calls
(ingest/espn_client.py owns those) -- this module owns exactly: validating
and persisting a connect attempt, saving which team is the user's, and
reporting connection state, each against the app_user columns
db/migrations/0004_league_connection.sql adds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from ingest.db import ingest_league
from ingest.espn_client import EspnFetchError, fetch_live_league
from sim.api.crypto import encrypt_credential


class LeagueConnectionError(ValueError):
    """The live ESPN fetch failed (wrong league id, bad/expired cookies,
    ESPN unreachable). Safe to show verbatim to the requesting user --
    there's no other account's existence to protect here, unlike
    auth_view's uniform login errors."""


class UnknownTeamError(ValueError):
    """team_id isn't one of the connected league's real teams."""


class NoConnectedLeagueError(ValueError):
    """The user has no espn_league_id yet -- set_team was called before
    connect_league."""


@dataclass(frozen=True)
class TeamOption:
    team_id: int
    name: str


@dataclass(frozen=True)
class ConnectionState:
    league_id: int | None
    season_id: int | None
    team_id: int | None
    connected_at: datetime | None


def resolve_current_season_id(now: datetime) -> int:
    """The ESPN fantasy season id is the season's start year (e.g. the 2026
    season runs Sept 2026 - Jan 2027 and is season_id=2026). Using the
    current calendar year is a deliberate simplification -- a user
    connecting in the Jan-Feb tail of the previous season would get the
    just-started, still-empty upcoming season instead. Historical-season
    selection is out of scope for this phase (see the design doc's Known
    Gaps)."""
    return now.year


def connect_league(
    conn: psycopg.Connection[Any],
    user_id: int,
    league_id: int,
    espn_s2: str | None,
    swid: str | None,
    now: datetime,
) -> tuple[TeamOption, ...]:
    """Validates by making one live ESPN fetch with the *submitted*
    credentials (nothing is persisted on failure), then on success:
    encrypts and saves the credentials plus league_id/season_id onto
    app_user, ingests the league via the existing, unchanged
    ingest_league(), and returns the team list read back from what was
    just ingested -- one live ESPN call total."""
    season_id = resolve_current_season_id(now)
    try:
        raw = fetch_live_league(league_id, season_id, espn_s2, swid)
    except EspnFetchError as exc:
        raise LeagueConnectionError(str(exc)) from exc

    summary = ingest_league(conn, raw, ingested_at=now)

    encrypted_s2 = encrypt_credential(espn_s2) if espn_s2 else None
    encrypted_swid = encrypt_credential(swid) if swid else None

    with conn.transaction():
        conn.execute(
            """
            UPDATE app_user
            SET espn_league_id = %s, espn_season_id = %s, espn_team_id = NULL,
                espn_s2_encrypted = %s, espn_swid_encrypted = %s,
                league_connected_at = %s
            WHERE user_id = %s
            """,
            (league_id, season_id, encrypted_s2, encrypted_swid, now, user_id),
        )

    return tuple(TeamOption(team_id=t.team_id, name=t.name) for t in summary.teams)


def set_team(conn: psycopg.Connection[Any], user_id: int, team_id: int) -> None:
    state = get_connection_state(conn, user_id)
    if state.league_id is None or state.season_id is None:
        raise NoConnectedLeagueError("no league connected yet")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM team WHERE league_id = %s AND season_id = %s AND team_id = %s",
            (state.league_id, state.season_id, team_id),
        )
        if cur.fetchone() is None:
            raise UnknownTeamError(f"team_id={team_id} is not a real team in this league")

    with conn.transaction():
        conn.execute(
            "UPDATE app_user SET espn_team_id = %s WHERE user_id = %s",
            (team_id, user_id),
        )


def get_connection_state(conn: psycopg.Connection[Any], user_id: int) -> ConnectionState:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT espn_league_id, espn_season_id, espn_team_id, league_connected_at
            FROM app_user WHERE user_id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()
    assert row is not None
    return ConnectionState(
        league_id=row[0], season_id=row[1], team_id=row[2], connected_at=row[3]
    )


def list_teams_for_league(
    conn: psycopg.Connection[Any], league_id: int, season_id: int
) -> tuple[TeamOption, ...]:
    """Reads the already-ingested team list straight from Postgres -- no
    ESPN call. Used by GET /leagues/me to re-render the team picker (e.g.
    after a page refresh) without needing the client to have kept the list
    connect_league originally returned."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT team_id, name FROM team WHERE league_id = %s AND season_id = %s ORDER BY team_id",
            (league_id, season_id),
        )
        rows = cur.fetchall()
    return tuple(TeamOption(team_id=r[0], name=r[1]) for r in rows)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest sim/tests/test_api_league_connection.py -v`
Expected: 8 passed

- [ ] **Step 5: Typecheck and lint**

Run: `mypy --strict sim/api/league_connection_view.py sim/tests/test_api_league_connection.py && ruff check sim/api/league_connection_view.py sim/tests/test_api_league_connection.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add sim/api/league_connection_view.py sim/tests/test_api_league_connection.py
git commit -m "Add league connection business logic: connect_league, set_team, get_connection_state"
```

---

### Task 5: HTTP routes — `/leagues/connect`, `/leagues/team`, `/leagues/me`

**Files:**
- Modify: `sim/api/app.py`
- Test: `sim/tests/test_api_league_connection.py`

**Interfaces:**
- Consumes: everything from `league_connection_view` (Task 4); `require_user`, `get_connection`, `_GENERIC_AUTH_ERROR`-style pattern (Phase A, `sim/api/app.py`).
- Produces (HTTP surface):
  - `POST /leagues/connect` `{league_id, espn_s2?, swid?}` → `200 {teams: [{team_id, name}]}` | `400` | `401`
  - `POST /leagues/team` `{team_id}` → `204` | `400` | `401`
  - `GET /leagues/me` (bearer token) → `200 {league_id, season_id, team_id, connected_at, teams}` | `401`

- [ ] **Step 1: Write the failing tests**

Add to `sim/tests/test_api_league_connection.py`. Add these imports at the top of the file, alongside the existing ones:

```python
from collections.abc import Iterator

from fastapi.testclient import TestClient

from sim.api import app as app_module

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)
```

(`os` and `DEFAULT_TEST_DSN` need importing too: add `import os` near the top, and `from ingest.db import DEFAULT_TEST_DSN` alongside the existing `ingest`-related imports.)

Add the `client` fixture (identical to `sim/tests/test_api_auth.py`'s):

```python
@pytest.fixture()
def client(
    pg_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    monkeypatch.setattr(app_module, "start_scheduler", lambda dsn=None: None)
    with TestClient(app_module.app) as test_client:
        yield test_client
```

Then append the HTTP tests:

```python
def test_connect_then_pick_team_over_http(
    client: TestClient, raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)

    signup_res = client.post(
        "/auth/signup", json={"email": "leaguehttp@example.com", "password": "a-real-password"}
    )
    headers = {"Authorization": f"Bearer {signup_res.json()['token']}"}

    me_before = client.get("/leagues/me", headers=headers)
    assert me_before.status_code == 200
    assert me_before.json()["league_id"] is None
    assert me_before.json()["teams"] == []

    connect_res = client.post(
        "/leagues/connect", json={"league_id": raw_fixture["id"]}, headers=headers
    )
    assert connect_res.status_code == 200
    teams = connect_res.json()["teams"]
    assert len(teams) == len(raw_fixture["teams"])

    me_after_connect = client.get("/leagues/me", headers=headers)
    assert me_after_connect.json()["league_id"] == raw_fixture["id"]
    assert me_after_connect.json()["team_id"] is None
    assert len(me_after_connect.json()["teams"]) == len(raw_fixture["teams"])

    team_res = client.post(
        "/leagues/team", json={"team_id": teams[0]["team_id"]}, headers=headers
    )
    assert team_res.status_code == 204

    me_final = client.get("/leagues/me", headers=headers)
    assert me_final.json()["team_id"] == teams[0]["team_id"]
    assert me_final.json()["teams"] == []


def test_leagues_connect_requires_auth(client: TestClient) -> None:
    res = client.post("/leagues/connect", json={"league_id": 12345})
    assert res.status_code == 401


def test_leagues_me_requires_auth(client: TestClient) -> None:
    assert client.get("/leagues/me").status_code == 401


def test_leagues_connect_returns_400_with_a_specific_message_on_espn_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise EspnAuthenticationError("bad cookies")

    monkeypatch.setattr(league_connection_view, "fetch_live_league", _raise)

    signup_res = client.post(
        "/auth/signup", json={"email": "leaguefail@example.com", "password": "a-real-password"}
    )
    headers = {"Authorization": f"Bearer {signup_res.json()['token']}"}

    res = client.post("/leagues/connect", json={"league_id": 12345}, headers=headers)
    assert res.status_code == 400
    assert "espn_s2" not in res.json()["detail"]  # never echoes the submitted credential


def test_leagues_team_rejects_a_fake_team_over_http(
    client: TestClient, raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)

    signup_res = client.post(
        "/auth/signup", json={"email": "leaguefaketeam@example.com", "password": "a-real-password"}
    )
    headers = {"Authorization": f"Bearer {signup_res.json()['token']}"}
    client.post("/leagues/connect", json={"league_id": raw_fixture["id"]}, headers=headers)

    res = client.post("/leagues/team", json={"team_id": 999999}, headers=headers)
    assert res.status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest sim/tests/test_api_league_connection.py -v`
Expected: FAIL — `404 Not Found` for `/leagues/connect` (route doesn't exist yet)

- [ ] **Step 3: Wire the routes into `sim/api/app.py`**

Add to the imports near the top (alongside `from sim.api import auth_view`):

```python
from sim.api import league_connection_view
```

Add these Pydantic models near the existing auth ones (`SignupRequest`, `LoginRequest`, etc.):

```python
class ConnectLeagueRequest(BaseModel):
    league_id: int
    espn_s2: str | None = None
    swid: str | None = None


class TeamOptionOut(BaseModel):
    team_id: int
    name: str


class ConnectLeagueResponseOut(BaseModel):
    teams: list[TeamOptionOut]


class SetTeamRequest(BaseModel):
    team_id: int


class LeagueConnectionOut(BaseModel):
    league_id: int | None
    season_id: int | None
    team_id: int | None
    connected_at: datetime | None
    teams: list[TeamOptionOut]
```

Add the routes after the existing `/auth/me` route:

```python
@app.post("/leagues/connect", response_model=ConnectLeagueResponseOut)
def connect_league_route(
    body: ConnectLeagueRequest,
    user: auth_view.AuthedUser = Depends(require_user),
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> ConnectLeagueResponseOut:
    now = datetime.now(UTC)
    try:
        teams = league_connection_view.connect_league(
            conn, user.user_id, body.league_id, body.espn_s2, body.swid, now
        )
    except league_connection_view.LeagueConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ConnectLeagueResponseOut(
        teams=[TeamOptionOut(team_id=t.team_id, name=t.name) for t in teams]
    )


@app.post("/leagues/team", status_code=204)
def set_team_route(
    body: SetTeamRequest,
    user: auth_view.AuthedUser = Depends(require_user),
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> None:
    try:
        league_connection_view.set_team(conn, user.user_id, body.team_id)
    except (
        league_connection_view.UnknownTeamError,
        league_connection_view.NoConnectedLeagueError,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/leagues/me", response_model=LeagueConnectionOut)
def get_leagues_me(
    user: auth_view.AuthedUser = Depends(require_user),
    conn: psycopg.Connection[Any] = Depends(get_connection),  # noqa: B008 (idiomatic FastAPI)
) -> LeagueConnectionOut:
    state = league_connection_view.get_connection_state(conn, user.user_id)
    teams: list[TeamOptionOut] = []
    if state.league_id is not None and state.season_id is not None and state.team_id is None:
        teams = [
            TeamOptionOut(team_id=t.team_id, name=t.name)
            for t in league_connection_view.list_teams_for_league(
                conn, state.league_id, state.season_id
            )
        ]
    return LeagueConnectionOut(
        league_id=state.league_id,
        season_id=state.season_id,
        team_id=state.team_id,
        connected_at=state.connected_at,
        teams=teams,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest sim/tests/test_api_league_connection.py -v`
Expected: 13 passed

- [ ] **Step 5: Run the full existing suite to confirm nothing broke**

Run: `pytest sim/tests ingest/tests -q`
Expected: all previously-passing tests still pass, plus this task's new ones

- [ ] **Step 6: Typecheck and lint**

Run: `mypy --strict sim ingest && ruff check sim ingest`
Expected: the same 21 pre-existing errors in `sim/engine.py`/`sim/tests/test_engine.py`, zero new; ruff clean

- [ ] **Step 7: Commit**

```bash
git add sim/api/app.py sim/tests/test_api_league_connection.py
git commit -m "Add /leagues/connect, /leagues/team, /leagues/me routes"
```

---

### Task 6: Recurring re-ingest job

**Files:**
- Create: `sim/api/reingest.py`
- Modify: `sim/api/scheduler.py`
- Test: `sim/tests/test_reingest.py`

**Interfaces:**
- Consumes: `fetch_live_league`, `EspnFetchError` (Task 3); `decrypt_credential` (Task 2); `ingest_league` (`ingest/db.py`).
- Produces: `reingest_user(conn, user_id: int, now: datetime) -> None`, `reingest_all_connected_users(conn, now: datetime | None = None) -> None`; `sim/api/scheduler.py` gains a second scheduled job.

- [ ] **Step 1: Write the failing tests**

Create `sim/tests/test_reingest.py`:

```python
"""Tests for sim.api.reingest -- the recurring background job that keeps
every connected user's league current. Mirrors sim/tests/test_api_league_
connection.py's approach: fetch_live_league is monkeypatched, never called
for real."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest

from ingest.espn_client import EspnFetchError
from sim.api import auth_view, league_connection_view, reingest
from sim.api.reingest import reingest_all_connected_users, reingest_user

FIXED_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _make_connected_user(
    pg_conn: psycopg.Connection[Any], email: str, raw_fixture: dict[str, Any]
) -> int:
    user = auth_view.create_user(pg_conn, email, "a-real-password", FIXED_NOW)
    league_connection_view.connect_league(
        pg_conn, user.user_id, raw_fixture["id"], None, None, FIXED_NOW
    )
    return user.user_id


def test_reingest_user_re_ingests_the_connected_league(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    user_id = _make_connected_user(pg_conn, "reingest1@example.com", raw_fixture)

    monkeypatch.setattr(reingest, "fetch_live_league", lambda *a, **k: raw_fixture)
    later = datetime(2026, 6, 2, tzinfo=timezone.utc)
    reingest_user(pg_conn, user_id, later)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT ingested_at FROM league WHERE league_id = %s AND season_id = %s",
            (raw_fixture["id"], 2026),
        )
        (ingested_at,) = cur.fetchone()
    assert ingested_at == later


def test_reingest_all_connected_users_skips_a_failing_user_and_continues(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(league_connection_view, "fetch_live_league", lambda *a, **k: raw_fixture)
    failing_user_id = _make_connected_user(pg_conn, "failing@example.com", raw_fixture)
    ok_user_id = _make_connected_user(pg_conn, "ok@example.com", raw_fixture)
    pg_conn.commit()

    call_count = {"n": 0}

    def _flaky_fetch(*args: Any, **kwargs: Any) -> Any:
        call_count["n"] += 1
        league_id = args[0]
        user_ids_by_league: dict[int, int] = {}
        # Both users share the same league_id in this test, so distinguish
        # by call order isn't reliable -- instead fail on the first call
        # only, succeed on the rest, to prove one failure doesn't abort
        # the batch regardless of which user_id it belongs to.
        if call_count["n"] == 1:
            raise EspnFetchError("simulated ESPN outage")
        return raw_fixture

    monkeypatch.setattr(reingest, "fetch_live_league", _flaky_fetch)

    reingest_all_connected_users(pg_conn, datetime(2026, 6, 2, tzinfo=timezone.utc))

    # No assertion on which specific user's fetch failed (both share a
    # league_id, so ingested_at ends up identical either way) -- the real
    # claim is that reingest_all_connected_users itself did not raise and
    # processed both users despite one EspnFetchError.
    assert call_count["n"] == 2
    assert failing_user_id != ok_user_id  # sanity: two distinct users were created
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest sim/tests/test_reingest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sim.api.reingest'`

- [ ] **Step 3: Write `sim/api/reingest.py`**

```python
"""Recurring re-ingest of every user's connected ESPN league (Auth Phase B)
-- sim/api/scheduler.py's second scheduled job, alongside precompute_all_
leagues. Mirrors sim/api/precompute.py's per-user error isolation: one
user's fetch failure (revoked cookies, a transient ESPN error) is logged
and skipped, never allowed to abort the rest of the batch.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import psycopg

from ingest.db import ingest_league
from ingest.espn_client import EspnFetchError, fetch_live_league
from sim.api.crypto import decrypt_credential

logger = logging.getLogger(__name__)


def reingest_user(conn: psycopg.Connection[Any], user_id: int, now: datetime) -> None:
    """Re-fetches and re-ingests one user's connected league. Raises
    EspnFetchError on failure -- reingest_all_connected_users catches it
    per user, this function itself does not swallow anything."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT espn_league_id, espn_season_id, espn_s2_encrypted, espn_swid_encrypted
            FROM app_user WHERE user_id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()
    assert row is not None
    league_id, season_id, encrypted_s2, encrypted_swid = row

    espn_s2 = decrypt_credential(encrypted_s2) if encrypted_s2 else None
    swid = decrypt_credential(encrypted_swid) if encrypted_swid else None

    raw = fetch_live_league(league_id, season_id, espn_s2, swid)
    ingest_league(conn, raw, ingested_at=now)


def reingest_all_connected_users(
    conn: psycopg.Connection[Any], now: datetime | None = None
) -> None:
    resolved_now = now or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("SELECT user_id FROM app_user WHERE espn_league_id IS NOT NULL")
        user_ids = [row[0] for row in cur.fetchall()]

    for user_id in user_ids:
        try:
            reingest_user(conn, user_id, resolved_now)
            conn.commit()
            logger.info("re-ingested league for user_id=%s", user_id)
        except EspnFetchError as exc:
            conn.rollback()
            logger.warning("skipped re-ingest for user_id=%s: %s", user_id, exc)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest sim/tests/test_reingest.py -v`
Expected: 2 passed

- [ ] **Step 5: Wire the job into `sim/api/scheduler.py`**

Find:
```python
from ingest.db import DEFAULT_DEV_DSN, connect, dsn_from_env
from sim.api.precompute import precompute_all_leagues

logger = logging.getLogger(__name__)

# Every 6 hours: frequent enough that cached title odds don't go stale for
# days, infrequent enough not to burn CPU running a 10,000-sim Monte Carlo
# per league constantly. A documented, adjustable operational choice, not a
# fitted/modelling parameter.
PRECOMPUTE_INTERVAL_HOURS = 6

JOB_ID = "precompute_all_leagues"


def _run_precompute_job(dsn: str) -> None:
    with connect(dsn) as conn:
        precompute_all_leagues(conn)
```
Replace with:
```python
from ingest.db import DEFAULT_DEV_DSN, connect, dsn_from_env
from sim.api.precompute import precompute_all_leagues
from sim.api.reingest import reingest_all_connected_users

logger = logging.getLogger(__name__)

# Every 6 hours: frequent enough that cached title odds don't go stale for
# days, infrequent enough not to burn CPU running a 10,000-sim Monte Carlo
# per league constantly. A documented, adjustable operational choice, not a
# fitted/modelling parameter.
PRECOMPUTE_INTERVAL_HOURS = 6

JOB_ID = "precompute_all_leagues"

# Same cadence as precompute -- no reason to invent a different one
# (Auth Phase B design doc). A user's connected league gets re-fetched from
# live ESPN this often, keeping scores/standings current through the
# season.
REINGEST_INTERVAL_HOURS = 6

REINGEST_JOB_ID = "reingest_all_connected_users"


def _run_precompute_job(dsn: str) -> None:
    with connect(dsn) as conn:
        precompute_all_leagues(conn)


def _run_reingest_job(dsn: str) -> None:
    with connect(dsn) as conn:
        reingest_all_connected_users(conn)
```

Then find:
```python
    resolved_dsn = dsn or dsn_from_env("DATABASE_URL", DEFAULT_DEV_DSN)
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _run_precompute_job,
        "interval",
        hours=PRECOMPUTE_INTERVAL_HOURS,
        args=[resolved_dsn],
        id=JOB_ID,
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "started precompute scheduler: every %sh, dsn=%s", PRECOMPUTE_INTERVAL_HOURS, resolved_dsn
    )
    return scheduler
```
Replace with:
```python
    resolved_dsn = dsn or dsn_from_env("DATABASE_URL", DEFAULT_DEV_DSN)
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _run_precompute_job,
        "interval",
        hours=PRECOMPUTE_INTERVAL_HOURS,
        args=[resolved_dsn],
        id=JOB_ID,
        replace_existing=True,
    )
    scheduler.add_job(
        _run_reingest_job,
        "interval",
        hours=REINGEST_INTERVAL_HOURS,
        args=[resolved_dsn],
        id=REINGEST_JOB_ID,
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "started precompute scheduler: every %sh, dsn=%s", PRECOMPUTE_INTERVAL_HOURS, resolved_dsn
    )
    logger.info(
        "started reingest scheduler: every %sh, dsn=%s", REINGEST_INTERVAL_HOURS, resolved_dsn
    )
    return scheduler
```

- [ ] **Step 6: Confirm the scheduler still starts cleanly with both jobs**

Run:
```bash
python3 -c "
from sim.api.scheduler import start_scheduler
s = start_scheduler('postgresql:///fantavo_dev')
print(sorted(job.id for job in s.get_jobs()))
s.shutdown(wait=False)
"
```
Expected: `['precompute_all_leagues', 'reingest_all_connected_users']`

- [ ] **Step 7: Typecheck and lint**

Run: `mypy --strict sim/api/reingest.py sim/api/scheduler.py sim/tests/test_reingest.py && ruff check sim/api/reingest.py sim/api/scheduler.py sim/tests/test_reingest.py`
Expected: no errors

- [ ] **Step 8: Run the full existing suite to confirm nothing broke**

Run: `pytest sim/tests ingest/tests -q`
Expected: all previously-passing tests still pass, plus this task's new ones

- [ ] **Step 9: Commit**

```bash
git add sim/api/reingest.py sim/api/scheduler.py sim/tests/test_reingest.py
git commit -m "Add recurring re-ingest job for connected leagues"
```

---

### Task 7: Web types and API client functions

**Files:**
- Modify: `web/lib/types.ts`
- Modify: `web/lib/api.ts`

**Interfaces:**
- Produces:
  - `export interface TeamOption { team_id: number; name: string }`
  - `export interface ConnectLeagueResponse { teams: TeamOption[] }`
  - `export interface LeagueConnection { league_id: number | null; season_id: number | null; team_id: number | null; connected_at: string | null; teams: TeamOption[] }`
  - `postLeaguesConnect(token: string, body: { league_id: number; espn_s2?: string; swid?: string }): Promise<ConnectLeagueResponse>`
  - `postLeaguesTeam(token: string, teamId: number): Promise<void>`
  - `getLeaguesMe(token: string): Promise<LeagueConnection>`

- [ ] **Step 1: Add the types**

In `web/lib/types.ts`, add near the existing `AuthUser`/`AuthResponse` types:

```typescript
/** Mirrors sim.api.app.TeamOptionOut. */
export interface TeamOption {
  team_id: number;
  name: string;
}

/** Mirrors sim.api.app.ConnectLeagueResponseOut. */
export interface ConnectLeagueResponse {
  teams: TeamOption[];
}

/**
 * Mirrors sim.api.app.LeagueConnectionOut. `league_id`/`season_id`/
 * `team_id`/`connected_at` are all null together before a user has
 * connected a league. `teams` is non-empty only while a league is
 * connected but no team has been picked yet -- it is what
 * /connect-league/pick-team renders.
 */
export interface LeagueConnection {
  league_id: number | null;
  season_id: number | null;
  team_id: number | null;
  connected_at: string | null;
  teams: TeamOption[];
}
```

- [ ] **Step 2: Extend `authedFetch` to support a JSON body**

In `web/lib/api.ts`, find:

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
```
Replace with:
```typescript
async function authedFetch(
  path: string,
  token: string,
  method: "GET" | "POST",
  body?: unknown,
): Promise<Response> {
  const url = new URL(path, API_BASE);

  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      cache: "no-store",
    });
  } catch (cause) {
```
(The rest of the function — error handling and the final `return res;` — is unchanged.)

- [ ] **Step 3: Add the import and the 3 new functions**

In `web/lib/api.ts`, extend the existing `import type {...} from "@/lib/types"` block to include `ConnectLeagueResponse, LeagueConnection,` (alphabetical, alongside the existing entries).

Append near the bottom, after the existing `getAuthMe` function:

```typescript
export async function postLeaguesConnect(
  token: string,
  body: { league_id: number; espn_s2?: string; swid?: string },
): Promise<ConnectLeagueResponse> {
  const res = await authedFetch("/leagues/connect", token, "POST", body);
  return (await res.json()) as ConnectLeagueResponse;
}

export async function postLeaguesTeam(token: string, teamId: number): Promise<void> {
  await authedFetch("/leagues/team", token, "POST", { team_id: teamId });
}

export async function getLeaguesMe(token: string): Promise<LeagueConnection> {
  const res = await authedFetch("/leagues/me", token, "GET");
  return (await res.json()) as LeagueConnection;
}
```

- [ ] **Step 4: Typecheck and lint**

Run (from `/web`): `npx tsc --noEmit && npx eslint lib/types.ts lib/api.ts`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
cd web && git add lib/types.ts lib/api.ts
git commit -m "Add league connection types and API client functions"
```

---

### Task 8: `getLeagueConnection()` and the 2 league Route Handlers

**Files:**
- Create: `web/lib/leagueConnection.ts`
- Create: `web/app/api/leagues/connect/route.ts`
- Create: `web/app/api/leagues/team/route.ts`

**Interfaces:**
- Consumes: `LeagueConnection`, `ConnectLeagueResponse`, `postLeaguesConnect`, `postLeaguesTeam`, `getLeaguesMe`, `ApiError` (Task 7); `SESSION_COOKIE_NAME` (Phase A, `web/lib/auth.ts`).
- Produces:
  - `getLeagueConnection(): Promise<LeagueConnection | null>`
  - `POST /api/leagues/connect` — proxies to sim, returns `{teams}` on success
  - `POST /api/leagues/team` — proxies to sim, returns `{ok: true}` on success

- [ ] **Step 1: Write `web/lib/leagueConnection.ts`**

```typescript
import "server-only";
import { cache } from "react";
import { cookies } from "next/headers";
import { getLeaguesMe } from "@/lib/api";
import { SESSION_COOKIE_NAME } from "@/lib/auth";
import type { LeagueConnection } from "@/lib/types";

/**
 * Mirrors web/lib/auth.ts's getCurrentUser(): reads the session cookie and
 * asks the sim API for this user's league-connection state. Returns null
 * if there's no session, or if the sim API call fails for any reason --
 * fails closed to "not connected," the safe side for every caller here
 * (the fallback is always "show the connect flow," never "show someone
 * else's league").
 */
export const getLeagueConnection = cache(async (): Promise<LeagueConnection | null> => {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE_NAME)?.value;
  if (!token) return null;
  try {
    return await getLeaguesMe(token);
  } catch {
    return null;
  }
});
```

- [ ] **Step 2: Write the connect route handler**

Create `web/app/api/leagues/connect/route.ts`:

```typescript
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { ApiError, postLeaguesConnect } from "@/lib/api";
import { SESSION_COOKIE_NAME } from "@/lib/auth";

export async function POST(request: Request) {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE_NAME)?.value;
  if (!token) {
    return NextResponse.json({ error: "not signed in" }, { status: 401 });
  }

  let body: { league_id?: number; espn_s2?: string; swid?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  if (!body.league_id) {
    return NextResponse.json({ error: "league_id is required" }, { status: 400 });
  }

  try {
    const result = await postLeaguesConnect(token, {
      league_id: body.league_id,
      espn_s2: body.espn_s2 || undefined,
      swid: body.swid || undefined,
    });
    return NextResponse.json(result);
  } catch (error) {
    const status = error instanceof ApiError && error.status >= 400 ? error.status : 502;
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: message }, { status });
  }
}
```

- [ ] **Step 3: Write the team route handler**

Create `web/app/api/leagues/team/route.ts`:

```typescript
import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { ApiError, postLeaguesTeam } from "@/lib/api";
import { SESSION_COOKIE_NAME } from "@/lib/auth";

export async function POST(request: Request) {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE_NAME)?.value;
  if (!token) {
    return NextResponse.json({ error: "not signed in" }, { status: 401 });
  }

  let body: { team_id?: number };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  if (body.team_id === undefined) {
    return NextResponse.json({ error: "team_id is required" }, { status: 400 });
  }

  try {
    await postLeaguesTeam(token, body.team_id);
    return NextResponse.json({ ok: true });
  } catch (error) {
    const status = error instanceof ApiError && error.status >= 400 ? error.status : 502;
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: message }, { status });
  }
}
```

- [ ] **Step 4: Typecheck and lint**

Run (from `/web`): `npx tsc --noEmit && npx eslint lib/leagueConnection.ts app/api/leagues/connect/route.ts app/api/leagues/team/route.ts`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
cd web && git add lib/leagueConnection.ts app/api/leagues/connect app/api/leagues/team
git commit -m "Add getLeagueConnection() and league connection Route Handlers"
```

---

### Task 9: `/connect-league` page

**Files:**
- Create: `web/app/connect-league/page.tsx`
- Create: `web/components/connect-league/connect-league-form.tsx`

**Interfaces:**
- Consumes: `getCurrentUser` (Phase A), `getLeagueConnection` (Task 8), `Button`/`Input`/`Card*` (existing UI primitives).

- [ ] **Step 1: Write the form component**

Create `web/components/connect-league/connect-league-form.tsx`:

```tsx
"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/**
 * Posts to /api/leagues/connect. On success, navigates to
 * /connect-league/pick-team -- that page independently fetches the team
 * list via getLeagueConnection() (GET /leagues/me), so no client-side
 * state needs to be threaded through the navigation.
 */
export function ConnectLeagueForm() {
  const router = useRouter();
  const [leagueId, setLeagueId] = useState("");
  const [espnS2, setEspnS2] = useState("");
  const [swid, setSwid] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setStatus("loading");
    setErrorMessage(null);
    try {
      const res = await fetch("/api/leagues/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          league_id: Number(leagueId),
          espn_s2: espnS2 || undefined,
          swid: swid || undefined,
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        throw new Error(body.error ?? `HTTP ${res.status}`);
      }
      router.push("/connect-league/pick-team");
    } catch (error) {
      setStatus("error");
      setErrorMessage(error instanceof Error ? error.message : "Unknown error");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <label htmlFor="connect-league-id" className="text-sm font-medium text-foreground">
          League ID
        </label>
        <Input
          id="connect-league-id"
          type="text"
          inputMode="numeric"
          required
          value={leagueId}
          onChange={(e) => setLeagueId(e.target.value)}
          disabled={status === "loading"}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <label htmlFor="connect-espn-s2" className="text-sm font-medium text-foreground">
          espn_s2 <span className="text-muted-foreground">(private leagues only)</span>
        </label>
        <Input
          id="connect-espn-s2"
          type="text"
          value={espnS2}
          onChange={(e) => setEspnS2(e.target.value)}
          disabled={status === "loading"}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <label htmlFor="connect-swid" className="text-sm font-medium text-foreground">
          SWID <span className="text-muted-foreground">(private leagues only)</span>
        </label>
        <Input
          id="connect-swid"
          type="text"
          value={swid}
          onChange={(e) => setSwid(e.target.value)}
          disabled={status === "loading"}
        />
      </div>
      <p className="text-xs text-muted-foreground">
        Public leagues need no cookies. For a private league, copy espn_s2 and
        SWID from your browser&apos;s cookies while signed into espn.com.
      </p>
      {status === "error" && errorMessage && (
        <p className="flex items-center gap-1.5 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          {errorMessage}
        </p>
      )}
      <Button type="submit" disabled={status === "loading"} className="cursor-pointer">
        {status === "loading" && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
        Connect league
      </Button>
    </form>
  );
}
```

- [ ] **Step 2: Write the page**

Create `web/app/connect-league/page.tsx`:

```tsx
import { redirect } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConnectLeagueForm } from "@/components/connect-league/connect-league-form";
import { getCurrentUser } from "@/lib/auth";
import { getLeagueConnection } from "@/lib/leagueConnection";

export default async function ConnectLeaguePage() {
  const user = await getCurrentUser();
  if (!user) redirect("/login");

  const connection = await getLeagueConnection();
  if (connection?.league_id) {
    redirect(connection.team_id ? `/league/${connection.league_id}` : "/connect-league/pick-team");
  }

  return (
    <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 py-12">
      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-xl">Connect your ESPN league</CardTitle>
        </CardHeader>
        <CardContent>
          <ConnectLeagueForm />
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 3: Typecheck and lint**

Run (from `/web`): `npx tsc --noEmit && npx eslint app/connect-league/page.tsx components/connect-league/connect-league-form.tsx`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
cd web && git add app/connect-league/page.tsx components/connect-league/connect-league-form.tsx
git commit -m "Add /connect-league page and form"
```

---

### Task 10: `/connect-league/pick-team` page

**Files:**
- Create: `web/app/connect-league/pick-team/page.tsx`
- Create: `web/components/connect-league/team-picker-form.tsx`

**Interfaces:**
- Consumes: `getCurrentUser` (Phase A), `getLeagueConnection` (Task 8), `TeamOption` (Task 7).

- [ ] **Step 1: Write the team picker form**

Create `web/components/connect-league/team-picker-form.tsx`:

```tsx
"use client";

import { useState, type FormEvent } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { TeamOption } from "@/lib/types";

/**
 * Posts to /api/leagues/team. A hard navigation on success (matching
 * web/components/auth/login-form.tsx's pattern) so the destination
 * /league/{id} layout's getCurrentUser()/league data re-renders
 * server-side against the just-picked team, instead of risking a stale
 * client-cached RSC payload.
 */
export function TeamPickerForm({ leagueId, teams }: { leagueId: number; teams: TeamOption[] }) {
  const [selectedTeamId, setSelectedTeamId] = useState<number | null>(teams[0]?.team_id ?? null);
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (selectedTeamId === null) return;
    setStatus("loading");
    setErrorMessage(null);
    try {
      const res = await fetch("/api/leagues/team", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ team_id: selectedTeamId }),
      });
      if (!res.ok) {
        const body = await res.json();
        throw new Error(body.error ?? `HTTP ${res.status}`);
      }
      window.location.href = `/league/${leagueId}`;
    } catch (error) {
      setStatus("error");
      setErrorMessage(error instanceof Error ? error.message : "Unknown error");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium text-foreground">Which team is yours?</legend>
        {teams.map((team) => (
          <label
            key={team.team_id}
            className="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm has-[:checked]:border-primary"
          >
            <input
              type="radio"
              name="team_id"
              value={team.team_id}
              checked={selectedTeamId === team.team_id}
              onChange={() => setSelectedTeamId(team.team_id)}
              disabled={status === "loading"}
            />
            {team.name}
          </label>
        ))}
      </fieldset>
      {status === "error" && errorMessage && (
        <p className="flex items-center gap-1.5 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          {errorMessage}
        </p>
      )}
      <Button
        type="submit"
        disabled={status === "loading" || selectedTeamId === null}
        className="cursor-pointer"
      >
        {status === "loading" && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
        Confirm
      </Button>
    </form>
  );
}
```

- [ ] **Step 2: Write the page**

Create `web/app/connect-league/pick-team/page.tsx`:

```tsx
import { redirect } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TeamPickerForm } from "@/components/connect-league/team-picker-form";
import { getCurrentUser } from "@/lib/auth";
import { getLeagueConnection } from "@/lib/leagueConnection";

export default async function PickTeamPage() {
  const user = await getCurrentUser();
  if (!user) redirect("/login");

  const connection = await getLeagueConnection();
  if (!connection?.league_id) redirect("/connect-league");
  if (connection.team_id) redirect(`/league/${connection.league_id}`);

  return (
    <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 py-12">
      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-xl">Pick your team</CardTitle>
        </CardHeader>
        <CardContent>
          <TeamPickerForm leagueId={connection.league_id} teams={connection.teams} />
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 3: Typecheck and lint**

Run (from `/web`): `npx tsc --noEmit && npx eslint app/connect-league/pick-team/page.tsx components/connect-league/team-picker-form.tsx`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
cd web && git add app/connect-league/pick-team/page.tsx components/connect-league/team-picker-form.tsx
git commit -m "Add /connect-league/pick-team page and form"
```

---

### Task 11: Root redirect logic — remove `DEFAULT_LEAGUE_ID`

**Files:**
- Modify: `web/app/page.tsx`

**Interfaces:**
- Consumes: `getCurrentUser` (Phase A), `getLeagueConnection` (Task 8).

- [ ] **Step 1: Write the new root page**

Replace the entire contents of `web/app/page.tsx`:

```tsx
import { redirect } from "next/navigation";
import { getCurrentUser } from "@/lib/auth";
import { getLeagueConnection } from "@/lib/leagueConnection";

/**
 * Auth Phase B: DEFAULT_LEAGUE_ID and the unconditional redirect it drove
 * are gone -- every signed-in user now lands on their own real connected
 * league. web/middleware.ts still gates /league/:path* on the session
 * cookie and sends an unauthenticated visitor to /login; this page adds
 * the next layer once signed in: no connection yet -> /connect-league,
 * connected but no team picked -> /connect-league/pick-team, both set ->
 * their real league.
 */
export default async function RootPage() {
  const user = await getCurrentUser();
  if (!user) redirect("/login");

  const connection = await getLeagueConnection();
  if (!connection?.league_id) redirect("/connect-league");
  if (!connection.team_id) redirect("/connect-league/pick-team");
  redirect(`/league/${connection.league_id}`);
}
```

- [ ] **Step 2: Remove the now-unused `DEFAULT_LEAGUE_ID` env var references**

Search for any other reference:
```bash
grep -rn "DEFAULT_LEAGUE_ID" web --include="*.ts" --include="*.tsx" | grep -v node_modules | grep -v .next
```
Expected: no output (this task's edit to `page.tsx` was the only reference). If any other file references it, stop and report — that is a real cross-file dependency this task's brief did not anticipate, not something to silently patch around.

- [ ] **Step 3: Typecheck and lint**

Run (from `/web`): `npx tsc --noEmit && npx eslint app/page.tsx`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
cd web && git add app/page.tsx
git commit -m "Replace DEFAULT_LEAGUE_ID redirect with connection-aware routing"
```

---

### Task 12: `docs/decisions.md` entry

**Files:**
- Modify: `docs/decisions.md`

Phase A's own final review flagged "no final `docs/decisions.md` task" as a plan gap worth fixing in the plan template (see `.superpowers/sdd/2026-08-14-auth-phase-a-identity/final-review.md`'s Recommendations #4, and the retroactive fix wave that added Phase A's own entry). This task applies that lesson proactively instead of leaving it to a post-hoc fix wave.

- [ ] **Step 1: Read the existing entries for format**

Read the end of `docs/decisions.md` (the most recent `## Phase N — ...` entries, including Phase A's own) to match heading format, bolded-lead-sentence-per-bullet style, and the closing `**Verification**:` bullet.

- [ ] **Step 2: Write the entry**

Append a new `## Phase 17 — Auth Phase B — league connection` section (confirm 17 is actually the next unused number by checking the current highest — Phase A landed as Phase 16 per its own fix-wave entry). Cover, at minimum:

- What shipped: real per-user ESPN league connection (public or private), encrypted credential storage (Fernet, server-held master key), a live ESPN fetch path (`ingest/espn_client.py`, the first production code to call ESPN live), the `/connect-league` → `/connect-league/pick-team` flow, and a recurring 6-hour re-ingest job. `DEFAULT_LEAGUE_ID` is gone.
- The deliberate scope limits: one league per user (no switcher), no disconnect/reconnect UI, no re-ingest health visibility — each with the one-line reasoning already captured in the design doc's "Known gaps" section.
- The `resolve_current_season_id` simplification (calendar year = season id) and its known edge case near the season boundary.
- The `GET /leagues/me` design refinement made during planning (not in the original spec's exact wording): it carries the pending team list itself (queried fresh from the already-ingested `team` table) rather than requiring the client to carry `POST /leagues/connect`'s response across a page navigation — simpler and refresh-safe, at the cost of one extra cheap DB read (never a second ESPN call) when `/connect-league/pick-team` loads.

- [ ] **Step 3: Commit**

```bash
git add docs/decisions.md
git commit -m "Record Auth Phase B decisions in docs/decisions.md"
```

---

### Task 13: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full Python test suite**

Run: `pytest sim/tests ingest/tests -q`
Expected: all tests pass (every pre-existing test plus every test this plan added across Tasks 2-6)

- [ ] **Step 2: Typecheck and lint the whole Python tree**

Run: `mypy --strict sim ingest && ruff check sim ingest db scripts`
Expected: the same pre-existing-only errors documented in Phase A's own Task 15 verification (21 mypy errors in `sim/engine.py`/`test_engine.py`, 11 ruff errors elsewhere) — zero new of either kind. If the count differs, investigate before proceeding; do not assume it's fine.

- [ ] **Step 3: Typecheck, lint, and build the web layer**

Run (from `/web`): `npx tsc --noEmit && npx eslint . && npm run build`
Expected: all three clean; the production build includes `/connect-league` and `/connect-league/pick-team` as real routes.

- [ ] **Step 4: Confirm the scheduler starts both jobs against the dev database**

Run:
```bash
python3 -c "
from sim.api.scheduler import start_scheduler
s = start_scheduler()
print(sorted(job.id for job in s.get_jobs()))
s.shutdown(wait=False)
"
```
Expected: `['precompute_all_leagues', 'reingest_all_connected_users']`

- [ ] **Step 5: Live verification against a real ESPN league — requires the project owner**

This step needs real ESPN credentials (a real `LEAGUE_ID`, and for a private league, real `ESPN_S2`/`SWID` cookies) and outbound network access — the same prerequisites `scripts/fetch_fixture.py` has always needed, and the one deliberate exception to this repo's "no live ESPN calls" rule (see the design doc's Verification section). This is not something to fabricate or skip silently: if you are an agentic worker without real ESPN credentials available, stop here and report that Steps 1-4 are verified and this step needs the project owner to run manually, rather than claiming it passed.

With real credentials available (`cp .env.example .env` filled in, `CREDENTIAL_ENCRYPTION_KEY` generated per Task 2's instructions, `uvicorn sim.api.app:app --port 8123` and the Next dev server both running):

1. Sign up or log in, land on `/connect-league`.
2. Submit a real public league's ID with no cookies. Confirm the team list renders on `/connect-league/pick-team`.
3. Pick a team, confirm redirect to `/league/{id}` showing real data (not a fixture).
4. Repeat with a real private league and real `espn_s2`/`SWID` cookies.
5. Submit a wrong league ID — confirm a specific, non-generic `400` message and that `GET /leagues/me` still shows no connection (nothing was persisted).
6. Submit a real private league's ID with no cookies (or wrong ones) — confirm a specific, non-generic `400` message distinguishing this from a bad league ID.

- [ ] **Step 6: Report**

Summarize what was verified automatically (Steps 1-4) and what remains for manual confirmation (Step 5) in the same style Phase A's Task 15 used in the SDD ledger.
