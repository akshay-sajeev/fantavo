# Fantasy Football Companion

Analytics layer on top of a user's ESPN fantasy football league. The differentiator is
a Monte Carlo season simulator; everything else is a view onto it.

## Repo layout

```
/web        Next.js 15 + TypeScript + Tailwind. UI only. No analytics logic.
/sim        Python 3.12 + NumPy. FastAPI service. Simulation + projections.
/ingest     Python. ESPN API client, normalization, DB writes.
/db         SQL migrations (Postgres).
/fixtures   Saved raw ESPN API responses. Used for all offline development.
/docs       product.md (feature spec), decisions.md (ADR log).
```

## Commands

```bash
make dev          # docker compose: postgres + sim service + next dev server
make test         # pytest (sim, ingest) + vitest (web)
make typecheck    # mypy --strict on /sim and /ingest, tsc --noEmit on /web
make lint         # ruff + eslint
make fixtures     # refresh /fixtures from live ESPN (requires .env, run rarely)
```

Always run `make test && make typecheck` before considering a task done.

## Hard rules

**Secrets.** `espn_s2` and `SWID` are user session cookies. Never log them, never
write them to fixtures, never include them in error messages or test files, never
commit `.env`. Redact them in any HTTP debug output. Scrub them from fixtures
before saving.

**Fixtures, not live calls.** All parsing and analytics work happens against
`/fixtures`. Do not hit the ESPN API during development, tests, or iteration —
it is undocumented, unofficial, and rate-limited. Only `make fixtures` calls out.

**One simulation engine.** `sim/engine.py::simulate_seasons()` is the only place
season outcomes are computed. Trade analysis, what-if scenarios, playoff odds, and
power rankings must all call it with different inputs. Never write a second
simulation path or an approximation shortcut — if a feature needs different output,
add a parameter or a post-processing function over the same result tensor.

**Seeds are mandatory.** Every stochastic function takes an explicit
`rng: np.Generator`. No module-level RNG, no implicit global seeding. Tests must be
deterministic.

**Distributions, not point estimates.** Player weeks are sampled from a fitted
distribution (mean, variance, availability probability). Never simulate using a
projection as a fixed value — that collapses all variance and produces meaningless
probabilities.

**No invented numbers.** Projection means come from the projections module, which
sources them from ESPN. Variance parameters come from fitted historical data in
`sim/params/`. Never hardcode a plausible-looking constant to make something work;
if a value is missing, raise and say so.

**Vectorize.** Simulations are NumPy array operations over shape
`(n_sims, n_weeks, n_teams)`. Do not write per-simulation Python loops.

## Domain glossary

ESPN's vocabulary is non-obvious. Getting these wrong causes silent, subtle bugs.

- **scoringPeriodId** — an NFL week. **matchupPeriodId** — a fantasy matchup, which
  may span multiple scoring periods in some league configs. They are not the same
  field and are not always equal.
- **segment** — 0 is the regular season. Playoff rounds use other values.
- **lineupSlotId** — position slot. Roughly: 0=QB, 2=RB, 4=WR, 6=TE, 16=D/ST, 17=K,
  20=Bench, 21=IR, 23=Flex. Verify against `mSettings` for the specific league
  rather than assuming; leagues customize slots.
- **A player's eligible slots ≠ their listed position.** Flex eligibility must be
  read from `eligibleSlots`, not inferred.
- League scoring is fully custom per league. Never assume standard or PPR — always
  compute from the league's `scoringSettings`.

## Conventions

- Store raw ESPN payloads as JSONB alongside normalized tables, so historical data
  can be reprocessed without re-fetching.
- Ingest is idempotent: re-running for the same week overwrites cleanly.
- Simulation results are precomputed by a scheduled job and cached. User-triggered
  scenarios (trades, what-ifs) run live with a smaller `n_sims`.
- Python: mypy strict, ruff, dataclasses over dicts for domain objects.
- No analytics logic in React components. The web layer renders what the API returns.

## Out of scope for now

Draft replay (requires modeling other managers' pick behavior — deliberately
deferred). Own projection models. Multi-platform support beyond ESPN.