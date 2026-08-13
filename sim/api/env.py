"""Minimal, non-destructive `.env` loader for the sim API service.

No other module in this codebase has ever needed to read `.env` itself --
`DATABASE_URL` has a safe non-secret default (`ingest.db.dsn_from_env`) and
every earlier phase's secrets (`ESPN_S2`/`SWID`) are only ever read by
`scripts/fetch_fixture.py`, a one-off CLI script the project owner runs by
hand (see that script's own `load_env`, which this mirrors). `GEMINI_API_KEY`
is the first secret the always-running FastAPI service itself needs, so
`uvicorn sim.api.app:app` must be able to pick it up from the repo-root
`.env` without the caller having to `export` it manually first.

Mirrors `scripts/fetch_fixture.py::load_env`'s exact parsing (plain
`KEY=VALUE` lines, `#`-comments skipped, quotes stripped) and its exact
safety property: `os.environ.setdefault`, never `os.environ[key] = value` --
a real shell-exported value always wins over `.env`, and nothing here ever
prints or logs a value, only sets it into the process environment. Per
CLAUDE.md's secrets rule (extended to this new secret, per this phase's
brief): this module must never log, print, or otherwise surface any value it
loads.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _REPO_ROOT / ".env"

_loaded = False


def load_dotenv_once() -> None:
    """Idempotent: parses `.env` into `os.environ` (via `setdefault`) at most
    once per process. Safe to call from multiple import sites (e.g. both
    `sim.api.app` at startup and a test module) without re-parsing."""
    global _loaded
    if _loaded:
        return
    _loaded = True

    if not _ENV_FILE.exists():
        return

    import os

    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
