# Fantavo

An analytics layer on top of a user's ESPN fantasy football league. The
differentiator is a Monte Carlo season simulator (`sim/engine.py`);
everything else — power rankings, playoff odds, trade analysis, an AI
league analyst — is a view onto its output.

## Repo layout

```
/web        Next.js 15 + TypeScript + Tailwind. UI only. No analytics logic.
/sim        Python 3.12 + NumPy. FastAPI service. Simulation + projections.
/ingest     Python. ESPN API client, normalization, DB writes.
/db         SQL migrations (Postgres).
/fixtures   Saved raw ESPN API responses. Used for all offline development.
/docs       decisions.md (ADR log), MASTER.md (design system reference).
```

## Setup

Requires Postgres, Python 3.12+, and Node.

```bash
brew services start postgresql@16
createdb fantavo_dev
createdb fantavo_test

cp .env.example .env   # fill in LEAGUE_ID / ESPN_S2 / SWID / GEMINI_API_KEY /
                        # CREDENTIAL_ENCRYPTION_KEY -- see the comments in
                        # .env.example for what each one is and how to get it

python3 -m pip install fastapi uvicorn "psycopg[binary]" pydantic numpy scipy \
  pytest mypy ruff apscheduler
python3 -m pip install -e .   # google-genai, argon2-cffi, cryptography, requests --
                               # the dependencies pyproject.toml actually declares
```

(Most of the first line's packages predate `pyproject.toml`'s `[project.dependencies]`
and were installed ad hoc rather than backfilled into it — see that file's own
comment for why. There is no lockfile; nothing here is version-pinned beyond
what `pyproject.toml` itself specifies.)

Apply migrations against `fantavo_dev` (and `fantavo_test` for the test
suite) — the sim API does not run them automatically on startup:

```bash
python3 -c "
from ingest.db import connect, run_migrations, DEFAULT_DEV_DSN
conn = connect(DEFAULT_DEV_DSN)
run_migrations(conn)
conn.commit()
conn.close()
"
```

`run_migrations` is idempotent, so re-running it after pulling new
migrations is always safe.

## Run locally

Sim API (binds to `127.0.0.1:8123`, not meant to be publicly reachable —
only the Next.js server talks to it):

```bash
uvicorn sim.api.app:app --host 127.0.0.1 --port 8123
```

Web app, from `/web` (see `web/README.md` for its own setup detail):

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

Then visit `http://localhost:3000` — sign up, connect your ESPN league (or
a public one to try it with no credentials), and pick your team.

## Tests and checks

```bash
pytest sim/tests ingest/tests -q
mypy --strict sim ingest
ruff check sim ingest db scripts

cd web
npx tsc --noEmit
npx eslint .
npm run build
```

## Refreshing fixtures

All parsing/analytics development and every test run against the saved
fixtures in `/fixtures` — nothing hits the live ESPN API except
`scripts/fetch_fixture.py`, run by hand, rarely:

```bash
python scripts/fetch_fixture.py
```

ESPN's fantasy API is undocumented and rate-limited; this script scrubs
credentials and personal identifiers before writing anything to disk (see
its own docstring for exactly what it does and doesn't redact).

## More detail

- `docs/decisions.md` — one line per non-obvious choice, appended at the
  end of each phase of work. The place to look for *why*, not just *what*.
- `docs/MASTER.md` — the design system reference (colors, typography,
  spacing) the UI is built against.
