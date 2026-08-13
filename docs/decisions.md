# Decisions

One line per non-obvious choice and why. Appended at the end of each phase.

## Phase 0 — Repo setup

- Repo had no git history yet, so `git init` was run fresh rather than assuming
  prior tracking; all files were moved with plain `mv` (nothing was tracked to
  warrant `git mv`).
- `.env.example` was not among the original flat files. Rather than guess its
  contents, it was added in a follow-up commit with empty `LEAGUE_ID`,
  `ESPN_S2`, `SWID` keys matching exactly what `scripts/fetch_fixture.py`
  reads via `os.environ`.
- Repo was pushed to GitHub as **public**, not private as this file's Phase 0
  section specifies. Deliberate deviation, confirmed with the project owner —
  left public rather than changed after the fact.
- `scripts/fetch_fixture.py`'s scrubber originally only pseudonymized GUIDs
  found under a dict key named `id`. ESPN also emits SWID-shaped GUIDs as bare
  list entries (`teams[].owners`) and plain fields (`teams[].primaryOwner`),
  which that version would have written to disk unscrubbed. Fixed to match any
  string value shaped like a GUID, regardless of which key holds it, and to
  report the JSON path of anything that still survives (`find_leaks`) instead
  of only aborting. Verified against the current `fixtures/league_raw_2026.json`:
  no secret key names and no bare identity GUIDs present — the only
  GUID-shaped strings left are ESPN CDN team-logo asset ids embedded inside
  `teams[].logo` URLs, which are not identity data.

## Phase 1 — Ingest

- `fixtures/league_raw_2026.json` is genuinely **pre-draft**:
  `draftDetail.drafted` is `false`, every `teams[].roster.entries` is empty,
  and all 192 draft picks have `playerId: -1`. The league is configured for
  12 teams (`settings.size`) but only 10 are claimed (`status.teamsJoined`;
  teams 11/12 have no `owners` key) — confirmed with a full 14-week
  round-robin-style schedule that already includes those two unclaimed
  slots as real opponents, so `LeagueParams` must be sized for 12 teams,
  not 10. `ingest.parse.build_team_params` therefore raises
  `RosterNotAvailableError` for every team against this fixture, by design
  — it never falls back to the free-agent pool or ADP to fabricate a
  lineup. This isn't a parser bug to work around; it's the true state of
  the league captured before the draft.
- One raw payload field was actively misleading and worth flagging for
  future ingest work: `schedule[].home/away.rosterForMatchupPeriod.entries`
  *looks* like a populated week-1 roster (e.g. Bijan Robinson appears there
  with `onTeamId: 6`), even though the team's real `roster.entries` is
  empty and the same player also appears in `_freeAgents` with
  `onTeamId: 0`. Treated as an ESPN preview/placeholder artifact, not
  authoritative roster data, and not used anywhere.
- Confirmed this league scores **full PPR** (1.0 pt/reception, statId 53)
  by computing it from `settings.scoringSettings.scoringItems`, not by
  reading a "scoringType" label or assuming a default. `ingest/scoring.py`
  builds a per-statId rate table (with `isReverseItem` folded into sign,
  and `pointsOverrides` applied by `lineupSlotId` for D/ST-specific stat
  categories) and reproduces ESPN's own `appliedTotal` to float precision
  for a real player at every position present in the fixture (QB, RB, WR,
  TE, K, D/ST) — see `ingest/tests/test_scoring.py`. `pointsOverrides` is
  keyed by `lineupSlotId`, which required an explicit
  `defaultPositionId -> lineupSlotId` map (`ingest/slots.py`) since those
  are two different ESPN numberings that happen to coincide only for RB
  and D/ST in this league's slot layout.
- Player projections use the stat block with `statSourceId=1`
  (projected), `statSplitTypeId=0` (season total), `seasonId` matching the
  fixture's own season — never a single scoring period or an actuals
  block. `PlayerProjection.mean_points_per_game` is our own computed
  season total (via the scoring table above) divided by
  `games_projected`, which is *not* a guess: it's ESPN's own assumed games
  count, recovered by inverting their `appliedTotal / appliedAverage`.
  Two players (Tyreek Hill, Brandon Aiyuk) have no such stat block at all,
  and two (Ricky Pearsall, James Conner) have one with `appliedTotal == 0`
  — all four are excluded from the pool as `SkippedPlayer` with a specific
  reason rather than silently dropped or given an invented positive mean.
  296 of 300 free agents end up with a usable projection.
- Player pool parsing includes a hard runtime cross-check: if our
  from-scratch scoring computation for a sample stat block diverges from
  ESPN's own `appliedTotal` by more than 1%, `parse_player_pool` raises
  rather than continuing with a silently-wrong scoring table.
- **STOP decision (sd / availability), resolved by the project owner:
  Option C.** `ingest` does not derive `sd` or `availability` at all.
  `PlayerProjection` stops at `mean_points_per_game` (plus the supporting
  `season_total` and `games_projected` fields); the full
  mean→(mean, sd, availability) mapping into `sim.engine.PlayerParams` is
  entirely Phase 2's job, sourced from fitted data in `sim/params/` per
  CLAUDE.md's variance rule. Two other options were on the table (a
  position-based coefficient-of-variation assumption, and a rank-source
  disagreement proxy for sd) — both were rejected because their constants
  would be un-fitted placeholders that Phase 2 would have to inspect,
  justify, or redo anyway.
- `games_projected / <NFL games per season>` was flagged as the strongest
  real-data candidate specifically for `availability`, independent of the
  sd question. It was still **not** implemented in this phase: the correct
  denominator is genuinely ambiguous from this fixture alone (the fantasy
  league's `scheduleSettings.matchupPeriodCount` + playoff rounds happens
  to sum to the same 17 as a real NFL team's regular-season game count,
  which is a coincidence, not a fact this fixture states directly) and
  getting that conflation wrong would be exactly the kind of silent,
  subtle bug CLAUDE.md warns about. `games_projected` is already stored on
  `PlayerProjection`, so Phase 2 has everything it needs to compute
  availability itself once the right denominator is settled, without this
  phase pre-committing to a possibly-wrong one.
- `ingest` package is fully `mypy --strict` clean on its own. Running
  `mypy --strict` over `sim` surfaces 20 pre-existing `ndarray`/`Any`
  typing gaps in `sim/engine.py` (missing type arguments on `np.ndarray`,
  a few `Returning Any` warnings) — these predate this phase, were not
  introduced by any ingest change, and were left untouched since modifying
  the single simulation engine wasn't in scope for this phase.
- No `Makefile` exists in the repo despite CLAUDE.md referencing `make
  test` / `make typecheck` / `make lint`. Ran the underlying commands
  directly (`pytest -q`, `mypy --strict ingest`, `ruff check ingest`)
  instead of blocking on a missing Makefile that Phase 0 didn't create.

## Phase 2 — Projection parameters

- **Pre-draft blocker confirmed, and re-escalated to the project owner
  before writing any code.** Re-verified directly against the fixture:
  `draftDetail.drafted` is `False`, all 12 teams have zero `roster.entries`,
  all 192 draft picks have `playerId: -1`. `PLAN.md`'s stated Phase 2 done
  criteria ("prints title odds for every team") is impossible to satisfy
  with real rosters from this fixture without fabricating a lineup, which
  CLAUDE.md's "no invented numbers" rule forbids just as much for rosters as
  for stats. Presented three options (validate against clearly-labeled
  synthetic rosters; defer the full pipeline demo until post-draft; check
  whether the real draft has since happened and re-fetch). **Decision:**
  Option A (synthetic rosters), confirmed by the project owner.
- **`sim/params/variance.py` fits a *position-level* coefficient of
  variation (CV = sd/mean), not a per-player week-to-week variance**,
  because this fixture has no per-week (`scoringPeriodId`) game log for any
  player -- ESPN's `_freeAgents` block only exposes season-aggregate stat
  blocks. The CV is fitted from real data: the cross-sectional dispersion of
  every usable player's actual prior-season (`seasonId = fixture's own
  seasonId - 1`, derived not hardcoded) per-game scoring rate
  (`appliedAverage`) at that position. This is a documented proxy, not a
  true fit of within-player variance -- the module docstring says so
  explicitly, including why (cross-player dispersion mixes talent/role
  differences with genuine game-to-game randomness) and what should replace
  it if ingest ever gains real weekly boxscores. Fitted CVs came out
  directionally sane against real fantasy intuition: RB 0.480, WR 0.400,
  D/ST 0.388, TE 0.363, QB 0.197, K 0.161 (skill positions spikier than
  QB/K, matching common experience) -- checked as an explicit non-hardcoded
  invariant in `sim/tests/test_params_variance.py`.
- **`sim/params/availability.py` uses `NFL_REGULAR_SEASON_GAMES = 17`** as
  the denominator for `games_projected / 17`, resolving the exact ambiguity
  Phase 1 flagged and deliberately left unresolved (risk of conflating the
  fantasy league's own `matchupPeriodCount`, which is 14 in this fixture,
  with the real NFL season length). 17 is a structural fact about the
  season being modeled (every NFL team has played a 17-game regular season
  since 2021), not a fitted or invented number, and is cross-checked against
  the fixture: >90% of usable players have `games_projected == 17`, and
  `n_regular_weeks (14) != NFL_REGULAR_SEASON_GAMES (17)` is asserted as a
  regression guard against the exact conflation risk Phase 1 raised.
  `derive_availability` raises rather than clips if a projection somehow
  exceeds 17 games, since that would indicate a wrong denominator or corrupt
  data, not a value to silently cap.
- **A minimum historical-sample-size guardrail (10 players) exists in
  `fit_position_cv`** before trusting a position's CV. This is a
  data-quality safety threshold, not a modelling parameter -- every position
  in the real fixture clears it by 2-8x, so it never actually changes
  behavior against `league_raw_2026.json`, but it stops a future
  differently-shaped fixture from silently fitting a CV off a handful of
  players.
- **Synthetic validation league (`sim/params/mock_rosters.py`,
  `sim/params/validate.py`), built per the project owner's Option A
  decision.** Every individual player's `PlayerParams` is real (same
  derivation as would apply to an actual roster); what's fake is which
  players get grouped onto which of 10 fabricated teams. Kept unmistakably
  separate from real predictions: every mock team name is literally
  `"Mock Team X (SYNTHETIC -- validation only)"`, both module docstrings
  open with a `SYNTHETIC DATA` banner, and every line `validate.py` prints
  repeats "SYNTHETIC" / "NOT a real forecast". This module is not imported
  by anything outside `sim/params`'s own tests and its own validation
  script -- nothing user-facing can accidentally surface it.
- **Mock rosters are built by a simulated snake draft** (best-player-
  available at each required lineup slot, real ESPN lineup-slot counts read
  from this fixture's own `settings.rosterSettings`) rather than by
  splitting the player pool into an arbitrary "best"/"worst"/"middle" bucket
  by hand. An earlier prototype that literally took the single best player
  at every position for one team and the single worst for another produced
  an absurd 76% title probability for the "best" team and a near-unplayable
  22-point-mean team for the "worst" -- an unrealistic talent concentration
  no real snake draft against 9 competitors would produce, and a clear
  violation of the plan's own 15-25%-ish sanity target. The snake draft
  (round order WR, RB, WR, RB, TE, FLEX, QB, D/ST, K -- interleaving WR/RB
  early specifically so the single best overall player at either spiky
  position doesn't land on one team) gives a small, plausible spread instead.
  Round order and lineup shape are cross-checked at runtime against this
  league's real `lineupSlotCounts` and raise loudly if they no longer match,
  rather than silently drafting the wrong slot counts.
- **Sanity check result: strongest mock roster's title odds = 27.5%**
  (seed 20260212, n_sims=10,000, 10-team mock league), against PLAN.md's
  rough 15-25% target and hard 35% bug threshold. Landed just above the
  rough target band but comfortably under the bug threshold.
  `sim/params/validate.py` explains the most likely cause in its own printed
  output rather than silently passing or hiding it: a 9-round (odd) snake
  draft structurally gives the team picking first one more "first pick of
  the round" than the team picking last, a known, harmless property of
  snake drafts with an odd round count -- not evidence of a variance-
  collapse bug. Chose to document this honestly rather than hand-tune the
  draft order further to force a number inside the rough band, which would
  itself have been a form of fitting a constant to "look right" -- exactly
  what CLAUDE.md's "no invented numbers" rule warns against.
- **Reused `sim.engine._sample_team_weeks` directly** (the literal function
  `simulate_seasons()` calls internally) to print per-week score
  distributions in `validate.py`, rather than writing a second sampling
  path. `SimulationResult` only exposes season-total `points_for`, not
  per-week scores, so this was the only way to satisfy the plan's
  "validation script that prints simulated team-week score distributions"
  requirement without duplicating sampling logic -- consistent with
  CLAUDE.md's "one simulation engine" rule (same code path, not a parallel
  model), while still needing every stochastic call site to take an
  explicit `rng`.
- Weekly team scores from the mock league land in the ~118-133 mean range
  with realistic-looking spread (p10/p90 roughly ±20 points), which reads
  as plausible full-PPR fantasy scoring to a human -- printed for exactly
  that kind of sanity check, not asserted as a golden value anywhere.
- `sim/params` is fully `mypy --strict` and `ruff check` clean on its own.
  Running `mypy --strict` over all of `sim` still surfaces the same 20
  pre-existing `ndarray`/`Any` typing gaps in `sim/engine.py` Phase 1 noted
  and left untouched (not introduced or touched by this phase). A whole-repo
  `ruff check sim ingest` also surfaces 3 pre-existing `TRY004` findings in
  `ingest/parse.py` and `ingest/scoring.py` (both from Phase 1, not touched
  by this phase) -- left as-is since `ingest` was out of scope for Phase 2
  and Phase 1 already recorded its own lint pass.
- New tests live under `sim/tests/test_params_*.py` rather than a
  `sim/params/tests/` package, since `pyproject.toml`'s `testpaths` already
  covers `sim/tests` and adding a new discovery root wasn't necessary.
  `sim/tests/test_engine.py` and its golden values were not touched.

## Phase 3 — Persistence

- **Local Postgres via Homebrew, exactly as the project owner pre-approved.**
  Ran `brew install postgresql@16` (no Docker/Postgres binary existed on this
  machine), `brew services start postgresql@16` to run it as a background
  launchd service, then `createdb fantavo_dev` and `createdb fantavo_test` --
  two separate databases so the idempotency test can truncate/own
  `fantavo_test` freely without disturbing a "real" local dev database.
  Connection is over the default Unix socket with peer auth (no password),
  so no secret handling is involved in DB connectivity at all.
- **DB client: `psycopg` (v3, with the `[binary]` extra) over `psycopg2` or
  an ORM (SQLAlchemy).** Chose psycopg3 for native type stubs (works cleanly
  under `mypy --strict` without a third-party stub package, unlike
  psycopg2), and no ORM because this phase's tables are a direct 1:1 mapping
  from `ingest.models` dataclasses to rows -- an ORM's mapping layer would
  duplicate types that already exist in `ingest/models.py` and `sim/engine.py`
  for no benefit at this scale, consistent with CLAUDE.md's "dataclasses
  over dicts" preference for domain objects rather than framework objects.
  Installed via `pip3 install "psycopg[binary]"`; no `requirements.txt` or
  `[project.dependencies]` exists yet in this repo (Phase 0-2 installed
  numpy/pytest/requests the same ad hoc way), so this phase follows the same
  pattern rather than introducing a new dependency-management convention
  unilaterally.
- **Migration tool: hand-rolled, not Alembic.** `db/migrations/*.sql` are
  plain numbered SQL files; `ingest.db.run_migrations` tracks which have
  been applied in a `schema_migrations` table and applies the rest in
  filename order, inside a transaction. For a single-developer project with
  one migration so far, Alembic's autogeneration/versioning machinery is
  overhead the repo doesn't need yet -- this can be swapped in later without
  changing the schema itself if migrations get complex enough to want it.
- **Schema grain is `(league_id, season_id, ...)` throughout**, not just
  `league_id`. A league's scoring settings, teams and player pool are all
  season-specific in ESPN's own data model (confirmed by this fixture:
  `seasonId` gates which stat block counts as "the" projection in
  `ingest.parse._find_season_projection`), and Phase 11 is already known to
  need multiple seasons per league (`leagueHistory`) -- keying everything by
  league_id alone would make that phase's very first write collide with
  this one.
- **`ingested_at` is a required argument to `ingest_league`, never a
  server-side `now()` default.** This is the load-bearing decision for the
  "byte-identical" done criterion: CLAUDE.md already requires every
  stochastic function to take an explicit `rng` instead of implicit global
  state (for reproducibility); the same reasoning applies to wall-clock time
  here -- an implicit `now()` would make two ingests of the identical
  fixture always differ by at least that one column, and "byte-identical"
  would be unachievable by construction, not just hard to test. A real
  ingest run (see `ingest/db.py`'s `_main` CLI) still passes
  `datetime.now(timezone.utc)`, once, itself, at the call site -- it's the
  library function that must not reach for it internally.
- **Idempotency strategy: upsert for the two singleton tables (`league`,
  `scoring_settings`, one row per league/season, via `INSERT ... ON
  CONFLICT DO UPDATE`), full delete-then-insert per league/season for the
  four child tables (`team`, `player`, `roster`, `matchup`).** Delete+insert
  was chosen over a more surgical `ON CONFLICT` upsert-plus-diff for the
  child tables because it is trivially correct for the "overwrites cleanly"
  requirement in CLAUDE.md's conventions: a player who drops out of the
  free-agent pool, or a roster entry that's removed, cannot leave a stale
  row behind, because nothing survives between the DELETE and the INSERT
  within the same transaction. Verified directly with a synthetic
  two-player fixture re-ingested with one player removed
  (`test_ingest_league_cleanly_removes_stale_child_rows`) -- the stale
  player's row is gone after the second ingest, not just unreferenced.
- **How the idempotency test actually verifies byte-identical state:**
  `ingest.db.canonical_dump(conn)` runs `SELECT * ... ORDER BY <all
  columns>` against every data table (`league`, `scoring_settings`, `team`,
  `player`, `roster`, `matchup`), serializes each row as
  `json.dumps(..., sort_keys=True, default=str)`, and joins everything into
  one `bytes` value. The test calls `ingest_league` twice with the identical
  fixture and the identical fixed `ingested_at`, taking a `canonical_dump`
  after each call, and asserts the two dumps compare equal with plain `==`
  -- a literal byte-for-byte comparison of a canonical serialization of
  every row in every table, not a row-count check or a hash. `sort_keys=True`
  matters specifically because Postgres's `jsonb` storage does not preserve
  the original key order of a stored JSON object (it reorders internally),
  so without it two runs with byte-identical *content* could still produce
  differently-ordered dict keys as a pure artifact of `jsonb` storage and
  falsely fail the comparison. A companion test
  (`test_reingesting_with_a_different_ingested_at_changes_the_dump`) proves
  the dump isn't vacuously equal for any two runs -- it does change when a
  real difference (a later `ingested_at`) is introduced, so the "identical"
  result in the actual idempotency test is meaningful, not a comparison that
  can never fail.
- **`canonical_dump`'s `ORDER BY` clause uses ordinal positions (`ORDER BY
  1, 2, 3, 4, 5`)** rather than named primary-key columns, so the same
  function works across tables with different schemas without a per-table
  branch. Every data table's first two columns are `(league_id, season_id)`
  and every table has at least 5 columns, so this is a safe, deterministic
  ordering everywhere it's used, not table-specific tuning.
- **Raw JSONB is stored at two grains, not one:** the full raw fixture
  payload on `league.raw_payload` (so the entire normalization pipeline can
  be rerun from scratch without re-fetching), *and* a narrower raw slice per
  child row (`team.raw_team`, `player.raw_player`, `matchup.raw_matchup`,
  `scoring_settings.raw_scoring_settings`) so a single entity can be
  reprocessed without deserializing the whole ~4.7MB fixture. This is some
  intentional duplication of bytes on disk; accepted as fine at the current
  single-league, single-season-in-dev scale. Revisit (e.g. store only the
  full payload and reprocess child rows from it on demand) if/when this
  covers many leagues and many seasons and the duplication becomes a real
  storage or write-latency cost.
- **`matchup` stores every raw schedule entry verbatim, including the
  `home/away.rosterForMatchupPeriod.entries` block Phase 1 already flagged
  as a misleading ESPN preview artifact** (docs/decisions.md Phase 1) that
  can look like a populated roster even pre-draft. Storing it is still
  correct -- persistence's job is to keep the source data for future
  reprocessing, not to pre-filter it -- but only `matchup_period_id`,
  `home_team_id`, `away_team_id` and `winner` are extracted into normalized
  columns, and the `roster` table (built from `teams[].roster.entries`, the
  actually-authoritative source) is the only place roster data is read from
  normalized columns. The migration's own comment repeats this caveat so a
  future reader of the schema doesn't have to go find the Phase 1 note to
  learn it.
- **`roster` stores every roster entry, bench and IR slots included**, not
  just starters -- filtering to a starting lineup is
  `ingest.parse.build_team_params`'s job when constructing `sim.engine`
  inputs, and persistence shouldn't bake that downstream decision into what
  gets kept. Against `fixtures/league_raw_2026.json` specifically this table
  ends up empty (0 rows) after ingest, which is the correct, honest result
  for a genuinely pre-draft league (`draftDetail.drafted` is `False`, every
  team's `roster.entries` is empty -- see Phase 1) -- not a bug in the sync
  logic. Verified explicitly in
  `test_ingest_league_persists_normalized_data`.
- **Test database setup: `fantavo_test`, a second Homebrew Postgres database
  separate from `fantavo_dev`.** `ingest/tests/test_db.py`'s `conn` fixture
  truncates every data table before each test (it owns `fantavo_test`
  outright) rather than relying on transaction rollback -- `ingest_league`
  commits internally between the "first ingest" and "second ingest" steps
  the idempotency test needs to observe as two genuinely separate,
  independently-visible states, which a single wrapping transaction that
  gets rolled back at the end would not allow inspecting the same way while
  still keeping each test isolated from the others.
- **DB tests skip (not fail) when Postgres is unreachable**, via
  `pytest.skip` with the exact setup commands in the message. This machine
  now has Postgres running and all 5 `ingest/tests/test_db.py` tests
  (including the byte-identical idempotency assertion) pass for real against
  it -- skip-on-unreachable is a courtesy for a future environment that
  hasn't run the Phase 3 setup steps, not a way to avoid actually running
  the test here.
- `DATABASE_URL` was added to `.env.example` as an optional, empty entry.
  Unlike `LEAGUE_ID`/`ESPN_S2`/`SWID` it is not a secret (a local
  Unix-socket connection string with no password), so `ingest/db.py`
  defaults to `postgresql:///fantavo_dev` / `postgresql:///fantavo_test` in
  code rather than requiring `.env` to be populated at all; the env var
  exists only so a future non-default DSN (e.g. Phase 4's API service) has
  somewhere conventional to override it from.
- `mypy --strict ingest` and `ruff check ingest db` both still show exactly
  the same pre-existing findings Phase 1/2 already documented and left
  alone (20 `sim/engine.py` `ndarray`/`Any` gaps pulled in transitively via
  `sim.engine` imports; 3 `TRY004` findings in `ingest/parse.py` and
  `ingest/scoring.py`) -- zero new findings from `ingest/db.py`,
  `ingest/tests/test_db.py`, or the `db/migrations/` SQL. Full suite:
  `pytest -q` is 79 passed (74 from Phases 0-2 plus 5 new in
  `ingest/tests/test_db.py`).

## Phase 4 — API

- **Pre-draft blocker, same precedent as Phase 2, applied to the DB layer
  this time.** `fixtures/league_raw_2026.json`'s real league still has zero
  drafted rosters, so `sim.api.params_loader.load_league` (which calls the
  real `ingest.parse.build_team_params` per team) genuinely cannot build a
  `LeagueParams` for it -- confirmed live: running the precompute job against
  it raises/skips with `RosterNotAvailableError`. Phase 4's done criterion
  needs *some* ingested league with a real `TeamParams` to curl against, so
  `scripts/ingest_synthetic_league.py` builds one: it reuses the identical
  snake draft `sim/params/mock_rosters.py` already runs for Phase 2's
  validation league (see `draft_mock_rosters`, factored out of
  `build_mock_league` for this reuse -- not a second draft implementation),
  then assembles a raw-ESPN-shaped payload (real `settings`, real
  `_freeAgents`, but `teams[].roster.entries` populated with those draft
  picks) and ingests it through the **real** `ingest.db.ingest_league` --
  not a shortcut around persistence, the actual production write path. The
  league is stored under `league_id = -1_990_001`
  (`scripts.ingest_synthetic_league.SYNTHETIC_LEAGUE_ID`), negative and
  therefore impossible for any real ESPN league to collide with, and named
  `"SYNTHETIC validation league (mock draft -- not a real league)"` with
  every team individually labelled `"... (SYNTHETIC -- validation only)"`,
  matching the unmistakability discipline Phase 2 established. This script
  is a local verification/dev tool (like `ingest/db.py`'s own `_main`), not
  wired into the API or into any user-facing code path.
- **Params-loading module (`sim/api/params_loader.py`) does not read
  `mean`/`sd`/`availability` back out of the normalized `player` table at
  all.** It reads `league.raw_payload` (the full raw ESPN JSON, stored
  verbatim by Phase 3) and re-runs the exact same pipeline every other
  phase already uses on it: `ingest.parse.parse_scoring_table` /
  `parse_player_pool` / `parse_teams` / `parse_schedule` /
  `build_team_params`, plus `sim.params.variance.fit_position_cv` and
  `sim.params.derive.derive_player_params_pool`. This is the
  "reprocessed without re-fetching" pattern CLAUDE.md's JSONB storage
  convention exists for, and it means the API contains **zero** independent
  parameter-derivation logic -- if API results ever diverged from a
  fixture-file run, that would be a bug in this thin loader, never a
  competing implementation of `ingest.parse` or `sim.params`. The one
  consequence: a league whose real roster isn't drafted yet still correctly
  raises `RosterNotAvailableError` through this path (verified against the
  real fixture above), exactly as it does for a fresh file-based run.
- **Cache storage: a new `simulation_cache` JSONB table
  (`db/migrations/0002_create_simulation_cache.sql`), one row per
  `(league_id, season_id)`, upserted in place** -- the natural fit per
  CLAUDE.md's existing JSONB-alongside-normalized-tables convention, no
  reason to deviate. Stores `n_sims`, `seed` and `computed_at` alongside the
  serialized `SimulationResult` (`sim/api/cache.py::serialize_result`) so a
  cached response is fully self-describing: a caller can see exactly which
  seed and sim count produced it without cross-referencing anything else.
  `serialize_result` keeps every per-team distribution
  (`finish_distribution` included), never collapsing to a bare scalar --
  CLAUDE.md's "distributions, not point estimates" rule applies to this
  response shape, not just to the sampling inside `simulate_seasons()`.
- **Seeding strategy (`sim/api/seeds.py`), two different rules for two
  different call sites, both explicit and documented in one place:**
  - Precomputed cache: `precompute_seed(league_id, season_id)` is a fixed,
    deterministic formula (`(league_id * 1_000_003 + season_id) %
    (2**32 - 1)`) -- not wall-clock-derived, not a per-league hardcoded
    constant. The same league always gets the same seed on any machine, at
    any time, which is exactly what makes "curl returns the same title odds
    as a direct engine call with the same seed" checkable rather than a
    one-off coincidence. The seed is always returned in the response's
    `seed` field and stored in `simulation_cache.seed`.
  - Live what-if: the caller may pass an explicit `seed` in the request
    body for a reproducible re-run; otherwise `draw_whatif_seed()` draws 32
    bits from `secrets.randbits` (OS entropy, same source
    `numpy.random.SeedSequence` itself would draw from) and returns the
    drawn value in the response. Never a bare, unseeded
    `np.random.default_rng()` call anywhere -- every `rng` construction
    site in `sim/api` has a traceable seed integer.
- **Scheduling library: APScheduler's `BackgroundScheduler`, not
  Celery/RQ/cron.** This is a single-process FastAPI service with no
  existing task queue or worker infrastructure; `BackgroundScheduler` runs
  an in-process thread alongside uvicorn with zero additional moving parts
  (no broker, no separate worker process) -- the right amount of
  infrastructure for this project's current single-instance scale. Runs
  `sim.api.precompute.precompute_all_leagues` immediately on API startup
  (so a freshly started service isn't serving an empty cache for a full
  interval) and then every 6 hours (`PRECOMPUTE_INTERVAL_HOURS`, a
  documented operational choice, not a fitted parameter). Explicitly noted
  as **not** safe for multiple API replicas (would N-times-run the job) --
  revisit with a real queue or DB-scheduled job if this service ever scales
  out horizontally.
- **`precompute_all_leagues` iterates every row in the `league` table and
  skips (with a log line, not a crash) any league that raises
  `RosterNotAvailableError`/`IngestError`/`ParamsError`** -- e.g. the real,
  still-pre-draft league. Verified live: running the job against
  `fantavo_dev` (with both the real league and the synthetic league
  ingested) produced exactly one cache row, for the synthetic league; the
  real league logged a skip with the same `RosterNotAvailableError` message
  `build_team_params` has always raised for it.
- **Route handlers accept an optional `season_id`** (query param on GET,
  body field on POST) rather than hardcoding one, because this schema's
  grain is `(league_id, season_id)` (Phase 3's deliberate choice), while
  the plan's URL shape (`/league/{id}/...`) has no season in it. When
  omitted, `params_loader.resolve_season_id` picks the most recently
  ingested season for that `league_id` (`MAX(season_id)`) rather than
  guessing or erroring -- for the single-season-per-league-in-dev case this
  phase and Phase 3 both operate under, that is unambiguous.
- **No connection pool.** `sim/api/app.py` opens one `psycopg` connection
  per request (via a FastAPI dependency, closed after the request) rather
  than adding `psycopg_pool` or a shared long-lived connection. At this
  project's current traffic scale (a single local dev user) the overhead is
  negligible and it avoids introducing pool lifecycle/sizing decisions this
  phase doesn't need yet; revisit if/when this becomes a real service under
  concurrent load.
- **What-if roster overrides are validated against the league's real
  ingested player pool, not against any fixed roster shape.** Every
  `player_id` in `roster_overrides` must resolve inside
  `LoadedLeague.players_by_id` (i.e. have a real derived `PlayerParams` for
  this league/season) or the request 422s with the specific unknown id --
  CLAUDE.md's "no invented numbers" rule applied to the HTTP layer: an
  unknown player is refused, never silently substituted or ignored. Full
  starting-lineup-shape validation (right position counts, right slot
  eligibility) is explicitly left to Phase 6's fuller trade/what-if UI, not
  duplicated here.
- **Error mapping, kept deliberately small:** `LeagueNotIngestedError` -> 404
  (league/season not in Postgres, or no cache row yet for GET);
  `IngestError`/`ParamsError` subclasses -> 409 (data exists but can't be
  turned into `LeagueParams` yet, e.g. no drafted roster); an unknown
  `roster_overrides` player id -> 422. Anything else propagates as FastAPI's
  default 500 rather than being caught and reshaped -- this phase doesn't
  try to anticipate every failure mode, only the ones CLAUDE.md's "no
  invented numbers" rule specifically requires surfacing clearly.
- **Test suite (`sim/tests/test_api_*.py`, `sim/tests/conftest.py`) follows
  Phase 3's exact skip-if-Postgres-unreachable convention**, reusing
  `ingest.db.DEFAULT_TEST_DSN` / `fantavo_test`, with a new shared
  `synthetic_league_id` fixture that ingests the SYNTHETIC league through
  the real `ingest_league` path once per test. One test
  (`test_load_league_matches_build_mock_league_rosters_exactly`) needed
  `pytest.approx(rel=1e-9)` instead of `==` for player `mean`/`sd`: Postgres
  JSONB does not guarantee bit-exact float64 round-tripping (it stores
  numeric literals as normalized text, not as an IEEE754 bit pattern), so a
  float that crosses the JSONB storage boundary can differ from its
  pre-storage value by roughly 1 ULP. This does not threaten the actual done
  criterion, because that comparison (see next point) never crosses that
  boundary asymmetrically -- both sides read the same stored row.
- **Done criterion, verified twice:** an automated HTTP-level test
  (`sim/tests/test_api_app.py::test_get_simulation_matches_a_direct_engine_call_with_the_same_seed`,
  via Starlette's `TestClient` driving the real ASGI app) and a live manual
  run against `fantavo_dev` with `uvicorn` actually listening on
  `127.0.0.1:8123`: `scripts/ingest_synthetic_league.py` ingested
  `league_id=-1990001`, `python -m sim.api.precompute` cached it with
  `seed=2857856903, n_sims=10000`, `curl
  http://127.0.0.1:8123/league/-1990001/simulation?season_id=2026` returned
  that exact seed and a per-team title-odds/finish-distribution array, and a
  separate direct `simulate_seasons()` call (loading params via
  `sim.api.params_loader.load_league` against the same DSN, same seed, same
  `n_sims`) reproduced every value bit-for-bit (`title odds match exactly:
  True`, `finish distributions match exactly: True`). Strongest team's title
  odds landed at 27.47%, consistent with Phase 2's own sanity-checked range
  for this same synthetic draft (27.5% at a different, also-deterministic
  seed) -- another cross-check that nothing in the DB round-trip silently
  changed the modelled distributions.
- **New dependencies (no `requirements.txt` exists yet, same ad hoc `pip3
  install` pattern Phases 0-3 used): `fastapi`, `uvicorn`, `apscheduler`,
  `httpx`** (the last only for `fastapi.testclient.TestClient` in tests,
  which depends on it). Added a `[[tool.mypy.overrides]]` for
  `apscheduler.*` in `pyproject.toml` (`ignore_missing_imports = true`) --
  it ships no `py.typed` marker or stubs, and this repo calls only a
  handful of well-documented methods on it (`add_job`, `start`,
  `shutdown`), not worth vendoring stubs for.
- `mypy --strict sim ingest` and `ruff check sim ingest db scripts` both
  show exactly the same pre-existing findings prior phases already
  documented (20 `sim/engine.py` `ndarray`/`Any` gaps, 1 pre-existing
  `sim/tests/test_engine.py` `dict` type-arg gap, 3 `TRY004` findings in
  `ingest/parse.py`/`ingest/scoring.py`) -- zero new findings anywhere under
  `sim/api/`, `sim/tests/test_api_*.py`, `sim/tests/conftest.py`, or
  `scripts/ingest_synthetic_league.py`. Full suite: `pytest -q` is 109
  passed (79 from Phases 0-3 plus 30 new: 7 in `test_api_seeds.py`, 5 in
  `test_api_synthetic_ingest.py`, 5 in `test_api_params_loader.py`, 3 in
  `test_api_cache.py`, 3 in `test_api_precompute.py`, 7 in
  `test_api_app.py`).

## Phase 5a — Design system

- **Generation mechanism: the `ui-ux-pro-max` skill's real `search.py` CLI**,
  invoked via `Skill` tool call `ui-ux-pro-max:design-system` (which surfaced
  the plugin's on-disk skill directory, including its `scripts/search.py` and
  its `data/*.csv` databases — 84 styles, 192 palettes, 74 font pairings, 25
  chart types, etc.), then run directly with `python3` and the documented
  `--design-system --persist` flags. No plugin-marketplace install was needed
  (`ui-ux-pro-max@ui-ux-pro-max-skill` was not in
  `~/.claude/plugins/installed_plugins.json`, only `superpowers` was) — the
  skill's files were already materialized under the Skill tool's own plugin
  runtime directory, and `search.py --help` confirmed a fully working CLI with
  real CSV-backed data behind it, so that was used as-is rather than layering
  on a redundant CLI install.
- **Query wording changed which BI/Analytics style the generator picked, so
  it was iterated deliberately rather than accepted on the first run.** The
  first pass (`"fantasy football analytics predictive dashboard data-dense
  probability forecasting"`) matched the **Data-Dense Dashboard** style, not
  PLAN.md's called-out best fit. Re-run with keywords weighted toward
  `"predictive analytics forecasting probability confidence intervals"`
  correctly matched **Predictive Analytics** (forecast lines, confidence
  intervals, trend projections, scenario modeling) — the right neighborhood
  per PLAN.md and the better fit for an app whose entire output is
  probability. Confirmed **Comparative Analysis Dashboard** and **Data-Dense
  Dashboard** both exist and were considered as secondary neighbors via
  `--domain style` lookups, but Predictive Analytics was kept as primary
  since forecast/confidence-band visual language matches this app's actual
  content (title odds, playoff odds, finish distributions) more directly than
  a generic KPI-grid framing.
- **Dials: `--density 8` (dashboard-tight spacing, 2px-32px scale),
  `--variance 4` (balanced/modern, not bold/asymmetric), `--motion 3`
  (subtle, scroll-reveal only).** Density 8 because this is explicitly a
  data-dense predictive dashboard per the brief, not a marketing page.
  Variance kept mid rather than high because a forecasting tool's credibility
  depends on the data reading clearly, not on bold/asymmetric layout risk.
  Motion kept low (subtle fade/scroll-reveal, no complex choreography)
  because the primary use case is a quick weekly check-in on a phone during a
  game, not an immersive first-visit experience.
- **Color palette accepted as generated: primary `#1E40AF` (blue), accent
  `#D97706` (amber, auto-adjusted by the tool from `#F59E0B` for a 3:1 WCAG
  contrast requirement), destructive `#DC2626` (red).** Blue-forward with an
  amber highlight is exactly the "trend/forecast vs. actual" pairing the
  Predictive Analytics style calls for, and leaves red free and unambiguous
  for risk/negative-delta indicators in the risk panel and trade-asymmetry
  framing (Phase 6), rather than overloading it as the primary brand color.
- **Typography accepted as generated: Fira Code (headings) + Fira Sans
  (body).** A monospace heading face is an unusual choice for a consumer app
  but fits a "precise, technical, data" mood better than a humanist sans for
  a probability-first tool where numbers (odds, percentages, point
  projections) are the primary content — tabular figures in a monospace-style
  face read as more trustworthy for exact numeric comparison, which is this
  app's whole job.
- **The generator's "Page Pattern" section is present in `MASTER.md` verbatim
  (`AI Personalization Landing`, from the tool's landing-page domain) but
  annotated in-place as non-governing.** `--design-system` always fills this
  section in from `landing.csv` even for a pure authenticated app dashboard
  with no marketing funnel — confirmed via `--domain product "fantasy sports
  analytics dashboard"`, whose own "Analytics Dashboard" product-type match
  says `Landing Page Pattern: N/A - Analytics focused`. Rather than silently
  deleting or hand-editing the tool's real output (which would misrepresent
  what the generator actually said), the section was kept intact with an
  explicit editorial note directing Phase 5b to the Style/Color/
  Typography/Component/Chart sections instead.
- **Chart recommendations queried separately per PLAN.md's explicit
  instruction, via two `--domain chart` searches** (one for
  "probability distribution confidence interval range forecast", one for
  "ranked comparison leaderboard ranking teams odds percentage"), plus one
  `--stack shadcn` query for chart-component integration guidance. Recorded
  as a new `## Chart Recommendations` section in `MASTER.md`: **Box Plot**
  for finish-position spread per team and **Line with Confidence Band** for
  any trajectory/projection-over-time view, both for the
  probability/distribution problem; **horizontal Bar Chart, sorted
  descending** for power rankings ordered by simulated title probability;
  and shadcn's `ChartContainer`/`chartConfig`/`ChartTooltip` wrapper pattern
  over raw Recharts, per the stack-specific guidance. This directly answers
  PLAN.md's stated core visual problem — showing shape, not a bare
  percentage — and gives 5b concrete chart types to build against instead of
  re-deriving them.
- **Persisted path corrected from the tool's default
  `design-system/<project-slug>/MASTER.md` (`design-system/fantavo/MASTER.md`
  and `design-system/fantavo/pages/`) to the flat `design-system/MASTER.md`
  and `design-system/pages/` PLAN.md's later phases actually reference**
  (Phase 6: "Check for `design-system/pages/whatif.md`"). The generator's own
  `--persist` help text nests output under a project-slug subfolder by
  default; every later phase in PLAN.md reads the flat path, so the
  generated files were moved (not regenerated) immediately after persisting,
  and the empty `pages/` directory kept with a `.gitkeep` so it survives the
  commit for later phases' page-specific overrides.
- No `Makefile` exists yet (same gap Phases 0-4 already noted) — this phase
  needed no test/typecheck/lint run since it touches no application code,
  only a design-token markdown file and a decisions-log entry.

## Phase 5b — Dashboard

- **API gap found before any UI work, escalated rather than worked around.**
  `sim/api/app.py` (Phase 4) returned only aggregate per-team simulation
  outcomes -- no player/roster/position/schedule/actual-game data of any
  kind -- which blocks 2 of PLAN.md's 3 required 5b features (dashboard
  rosters/schedule/current-matchup, and the entire risk panel). Presented
  the gap and two options (scope down vs. extend the API) before writing
  any frontend code; the project owner approved **Option B: an
  additive-only extension** to `sim/api/app.py` exposing data
  `sim.api.params_loader`/`ingest.parse` already compute in memory, with no
  engine changes and no second simulation path.
- **Two new routes, two new modules, following Phase 4's exact patterns
  (season_id resolution, `LeagueNotIngestedError`->404,
  `IngestError`/`ParamsError`->409):**
  - `GET /league/{id}/roster` (`sim/api/roster_view.py`): per-team
    starters/bench with player name, position, lineup slot, and the same
    `mean`/`sd`/`availability` `PlayerParams` already derives -- reruns the
    identical `ingest.parse` + `sim.params` pipeline `load_league` uses,
    just keeping the name/position metadata `load_league` normally
    discards, rather than adding a second derivation path.
  - `GET /league/{id}/schedule` (`sim/api/schedule_view.py`): built
    directly off the already-normalized `league`/`team`/`matchup` tables
    (no raw-payload reparsing needed, since `matchup_period_id`/
    `home_team_id`/`away_team_id`/`winner` are already extracted columns).
- **`floor`/`ceiling` on each roster player are the 10th/90th percentile of
  the *exact same* per-game Gamma(mean, sd) distribution
  `sim.engine._sample_team_weeks` samples from** -- same shape/scale
  formula, imported directly from `sim.engine` (`_gamma_shape_scale`)
  rather than re-derived, so the two can never silently drift apart.
  Computed with `scipy.stats.gamma.ppf` (closed-form inverse CDF, added a
  `scipy.*` mypy override alongside the existing `apscheduler.*` one) --
  no sampling happens, so this is not a second simulation path, just an
  analytic property of a distribution the engine already uses.
- **Team `risk_rating` and `positional_concentration` are plain, fully
  documented arithmetic over a team's own already-serialized starters**,
  computed in `sim/api/roster_view.py` (not `sim.engine`, not the
  frontend): `risk_rating = 1 - (Σ mean_i·availability_i)/(Σ mean_i)` (the
  fraction of a team's expected starting points exposed to weekly
  unavailability), and `positional_concentration` flags any starting
  position with zero same-position bench depth. Kept server-side
  specifically because CLAUDE.md's "no analytics logic in components" rule
  means the frontend must only render, never compute, a rollup like this --
  even a "simple" one.
- **`current_week` on the schedule response is derived purely from the
  `matchup` table's own `winner` column** (first week with an undecided
  matchup; `None` if every matchup is already decided) -- never a
  wall-clock guess or a fabricated week number. For today's synthetic
  league (`winner == "UNDECIDED"` everywhere), this correctly reports week
  1 as current/upcoming. Verified with a test that flips every `winner` to
  `"HOME"` and confirms `current_week` becomes `None`
  (`sim/tests/test_api_schedule.py`).
- **New backend tests**: `sim/tests/test_api_roster.py` (9 tests total
  across both new files) verify floor < mean < ceiling per player, the
  route matches a direct `load_team_rosters()` call exactly, risk_rating is
  0 when every starter is fully available, and positional_concentration
  flags exactly the positions with zero bench depth -- including an
  explicit assertion that the synthetic league's mock draft (which only
  fills starting slots, never bench) produces zero bench players for every
  team, a real property of that fixture, not a bug in the formula.
  `sim/tests/test_api_schedule.py` covers the 404 case, the all-undecided
  case, the all-decided case, and a route-vs-direct-call equivalence check.
  Full suite: 118 passed (109 from Phases 0-4 plus 9 new); `mypy --strict
  sim ingest` still shows only the same 21 pre-existing errors Phase 4
  documented (0 new); `ruff check sim ingest db scripts` shows only the
  same pre-existing findings (0 new) in files this phase didn't touch.
- **Next.js pinned to 15.5.23, not `create-next-app@latest`'s default
  (Next 16.3.0).** CLAUDE.md's repo layout explicitly states "Next.js 15";
  scaffolded with `create-next-app@latest` (the CLI itself, version
  16.3.0) and then downgraded `next`/`eslint-config-next` to `15.5.23`
  rather than deviate from the documented stack. Consequence: `npm audit`
  shows 3 high-severity advisories (postcss XSS/path-traversal, sharp/libvips
  CVEs) whose only fix is upgrading to Next 16 -- accepted for now since
  this is a local single-developer dev service, not a deployed target, and
  revisit if/when a real deploy is planned.
- **`eslint.config.mjs` uses `FlatCompat` from `@eslint/eslintrc`, not the
  direct `eslint-config-next/core-web-vitals` import `create-next-app`
  scaffolded.** `eslint-config-next@15.5.23` still ships its config in the
  legacy (eslintrc `extends`) format, not a flat-config array -- the direct
  subpath-import pattern the newer (Next 16) CLI generated doesn't work
  against this version. `FlatCompat` is the standard, documented bridge for
  exactly this situation.
- **Recharts stayed on the CLI-installed v3.8.0 (not downgraded to 2.x),
  after finding and fixing a real bug rather than working around it via a
  different library version.** The power-rankings horizontal bar chart
  initially rendered bars at wildly wrong, inconsistent widths (verified by
  inspecting rendered SVG path/rect geometry directly, not just visually) --
  root-caused to `<Bar>`'s default entrance animation getting stuck in an
  intermediate transition frame specifically when `<Cell>` children are
  used for per-bar coloring (a `layout="vertical"` + `Cell` + animation
  interaction, reproduced identically across a clean dev-server restart
  with cache cleared, ruling out HMR staleness). Fixed with
  `isAnimationActive={false}` on the `<Bar>`, which also happens to align
  with MASTER.md's own Motion dial (3/10, "subtle") -- this default
  animation was heavier than that dial calls for regardless. A brief
  detour attempted downgrading to `recharts@2.15.x` (avoided, since
  `components/ui/chart.tsx` -- shadcn's own generated wrapper -- imports
  v3-only exported types like `TooltipValueType` that don't exist in v2,
  which would have meant hand-patching the shadcn-generated file).
- **Design tokens resolve a naming collision between MASTER.md's "Accent/CTA"
  (a saturated amber, `#D97706`, meant for real CTA buttons) and shadcn's
  structural `--accent` token (a light neutral hover/selected tint used
  across every menu/tab/table-row interaction).** Wiring MASTER's amber
  directly into shadcn's `--accent` would have turned every generic hover
  state bright amber -- a busy, "ornate" result MASTER.md's own anti-pattern
  list forbids. Resolution: `--accent` stays a light neutral tint;
  MASTER's actual amber lives in a new `--brand-accent` token, used only
  for genuine CTAs/highlights (the "Upcoming" week badge, the champion
  segment in `FinishDistributionStrip`). Same treatment for MASTER's
  "Secondary" data-blue (`#3B82F6`): kept as its own `--data-blue` /
  chart-series token rather than shadcn's structural `--secondary`
  (ordinary secondary buttons stay a light neutral so they don't visually
  compete with real chart data).
  - **Contrast-driven deviation from MASTER.md's literal button CSS:**
    computed WCAG contrast for white text on `#D97706` (~3.2:1) and on
    `#3B82F6` (~3.7:1) -- both below the checklist's own 4.5:1 minimum for
    normal-size text, even though MASTER's own `.btn-primary` CSS snippet
    uses white text on the amber. `--brand-accent-foreground` uses dark
    text (`#1F2937`, >6:1 on amber) instead, documented in `globals.css`
    directly next to the token. A narrow, evidence-based deviation from the
    literal generated CSS in service of the design system's own stated
    accessibility requirement, not a stylistic override.
  - No dark-mode palette: MASTER.md specifies light-only tokens (no dark
    dial output), so no theme toggle is wired up in 5b; `globals.css` keeps
    shadcn's `.dark` class scaffold present but unused.
- **"Where roster strength and record disagree, say so" (PLAN.md), resolved
  honestly for a schedule with zero completed weeks.** `lib/standings.ts`
  tallies *actual* records by literally counting already-decided
  `schedule.winner` values per team (`"HOME"`/`"AWAY"`/`"TIE"`) -- treated
  as bookkeeping over facts the API already returned, not "analytics logic"
  in CLAUDE.md's sense (no probability, no weighting, nothing
  `simulate_seasons()` computes differently), the same class of operation
  as sorting an already-returned array. For today's synthetic league (every
  matchup `"UNDECIDED"`), `StandingsTable` detects zero games played
  league-wide and renders an explicit "no games have been played yet"
  banner instead of a fabricated comparison; the "record diverges from
  projected strength" flag is real code that activates automatically, on
  the same path, once real weekly results exist (Phase 9) -- no
  special-casing for synthetic vs. real data anywhere.
- **"Current matchup" defined as `schedule.current_week`'s matchups,
  explicitly labeled "Upcoming," never rendered as a result.** For a league
  with no completed weeks there is no played "current" game to show;
  fabricating one was explicitly ruled out. If `current_week` is `None`
  (every matchup decided), the dashboard shows a "Season complete" state
  instead of guessing a week number.
- **`FinishDistributionStrip` (a segmented/stacked horizontal bar) is used
  for `finish_distribution`, not a box plot**, even though MASTER.md's
  Chart Recommendations lists Box Plot first for "finish-position spread."
  `finish_distribution` is an already-discretized probability-mass array (one
  float per finish place, `n_playoff_teams + 1` buckets) returned by the
  API, not the raw per-simulation samples a box plot needs -- MASTER.md's
  own text calls out this exact secondary case ("a per-rank
  stacked/horizontal bar... is the natural secondary view when the
  discreteness of 'finish place' needs to be legible"). Applying the chart
  type that matches the *actual* API response shape, per Phase 5a's own
  guidance, rather than forcing a box plot onto data it doesn't fit.
- **Fonts loaded via `next/font/google` (self-hosted, no FOUC, no
  render-blocking request) rather than the `<link>`/`@import` MASTER.md's
  markdown literally shows** -- same two families (Fira Code for headings,
  Fira Sans for body), different, better-practice delivery mechanism for a
  Next.js app specifically.
- **No league-picker or auth flow.** Out of scope for 5b (not in PLAN.md's
  three required features). `/` redirects to `DEFAULT_LEAGUE_ID` (env var,
  defaults to the synthetic league). Every page under `/league/[leagueId]`
  is otherwise fully generic on that route param -- nothing in `/web`
  special-cases `leagueId === -1990001` or checks for "synthetic" in any
  way, so it will work unmodified once the project owner drafts and
  re-ingests the real league.
- **`lib/api.ts` is the only place `/web` talks to the sim API**, marked
  `import "server-only"` so an accidental client-component import is a
  build error rather than a silent leak of the internal service URL into
  the browser bundle. All three pages fetch via Server Components
  (`fetch(..., { cache: "no-store" })`); no SWR/React Query, since 5b has
  no live client-side interactivity (that starts in Phase 6's what-if UI).
- **Verification**: `npx tsc --noEmit`, `npx eslint .`, and `npm run build`
  (production build) all clean with zero errors/warnings. Backend:
  `pytest -q` 118 passed, `mypy --strict sim ingest` 21 pre-existing errors
  (0 new), `ruff check sim ingest db scripts` pre-existing findings only (0
  new). Visually verified in a real browser (Next dev server + uvicorn,
  both against `league_id=-1990001`) at 375/768/1024/1440px: dashboard
  (standings, current matchup, rosters, remaining schedule), power rankings
  (bar chart + full breakdown table), and roster risk (per-team cards with
  positional-concentration flags and per-player floor/ceiling bars) all
  render correctly; confirmed no page-level horizontal scroll at 375px via
  `document.documentElement.scrollWidth`; confirmed the 404/error path
  renders a readable message for an un-ingested league id.

## Phase 6 — What-if and trades

- **Trade and Roster swap needed nothing new backend-wise.** Both route
  through the existing `POST /league/{id}/whatif` endpoint (Phase 4) with
  different `roster_overrides` — the plan's own framing ("same engine with
  different inputs") held exactly. All the new backend work this phase went
  into Alternate lineup and Schedule neutrality instead.
- **Alternate lineup and Schedule neutrality hit the pre-draft blocker in a
  new, sharper form, and were resolved accordingly.** Both need per-week
  *actual* scores; `matchup` stores only a decidedness flag and a raw JSONB
  blob, and neither the real league (still pre-draft) nor the SYNTHETIC
  validation league (mock-drafted, never mock-*played*) has any actual
  weekly scoring data anywhere. Resolution: one sampled "actual" season is
  drawn from the exact same fitted Gamma(mean, sd) × availability model
  `simulate_seasons()` already uses, via `sim.engine._sample_player_weeks`
  — the literal internal helper the engine calls, not a second sampling
  implementation. This is explicitly a *different, stronger* category of
  fabrication than a synthetic roster grouping (it invents "what happened,"
  not just "who's on the team") — flagged as such rather than silently
  applying the same precedent, and proceeded on the reasoning that a single
  real draw from the real fitted model, clearly and permanently labeled
  (`SeasonReplayResponse.note`, `synthetic_actual_scores: true`), is still a
  real computation rather than an invented number. Every response surface
  this touches says "SYNTHETIC simulated season, not real results" — never
  presented as, or mistakable for, a played game.
- **`_sample_team_weeks` refactored into `_sample_player_weeks` +
  `.sum(axis=2)`, not duplicated.** The season-replay module needs
  individual player scores (to compare a bench player's realized week
  against a starter's), not just each team's already-summed total. Same RNG
  consumption order as before the refactor (same two `rng` calls, same
  sequence), so `sim/tests/test_engine.py`'s golden values are provably
  unaffected — verified by running the golden tests, not just asserted.
- **"Optimal lineup" solved exactly via `scipy.optimize.linear_sum_assignment`
  (Hungarian algorithm)** over a player × slot-instance cost matrix, with
  flex eligibility read from each player's real `eligibleSlots`
  (CLAUDE.md's rule, not inferred from position) — not a hand-rolled
  greedy/position-based heuristic that could silently pick a wrong lineup
  in an edge case. Deterministic combinatorial optimization over
  already-sampled numbers; the only randomness in the whole module is the
  one `_sample_player_weeks` call, so this isn't a second simulation path.
- **Schedule neutrality is a pure permutation/broadcast comparison** over
  the same per-team weekly score array Alternate-lineup already computed —
  no new sampling, no per-simulation Python loop, matching PLAN.md's "a
  permutation over existing weekly score arrays, not a new simulation"
  instruction literally.
- **Trade builder's before/after comparison reuses the same seed for both
  runs**, so the reported title/playoff-odds delta reflects only the roster
  change, not independent sampling noise between two separate live
  `simulate_seasons()` calls — stated explicitly in the UI copy so the
  number's meaning is legible, not just correct.
- **Verification**: `pytest -q` 124 passed (Phase 5b's 118 + 6 new), `mypy
  --strict sim ingest` 21 pre-existing errors (0 new), `ruff check sim
  ingest db scripts` pre-existing findings only (0 new — none touch any
  Phase 6 file). Web: `tsc --noEmit`, `eslint .`, and `npm run build`
  (production) all clean. Visually verified in a real browser (uvicorn +
  Next dev server, both against `league_id=-1990001`) at desktop and
  375px mobile: ran a live trade (Puka Nacua for Ja'Marr Chase) and
  confirmed both teams' title/playoff odds and finish distributions
  updated with the asymmetric-impact headline rendered plainly, not buried
  in a table; ran a live season replay and confirmed actual/optimal/
  schedule-neutral records rendered with the SYNTHETIC labeling visible;
  confirmed the roster-swap tab renders correctly; confirmed no
  page-level horizontal scroll at 375px.
- **Recovery note**: the agent that did this phase's implementation work
  hit a background session-limit failure after all code was written and
  tests were passing locally, but before verification/commit/push
  completed. The coordinating session verified the already-written work
  directly (tests, mypy, ruff, tsc, eslint, production build, live browser
  check against the real running stack) rather than re-doing it, found no
  issues, and completed the commit/push/decisions-log steps this section
  itself documents.

## No more mock data -- the real league has drafted

The project owner ran `python scripts/fetch_fixture.py` after their actual
draft and asked for the pre-draft/mock-data handling to be retired in favor
of the real league. This surfaced two real, previously-latent bugs -- not
edge cases invented for this update, but gaps that simply had no way to
trigger before real drafted rosters existed anywhere in this codebase.

- **`fixtures/league_raw_2026.json` is now genuinely post-draft**:
  `draftDetail.drafted` is `true`, all 128 draft picks have a real
  `playerId` (none are `-1`), and every one of the league's 8 teams (the
  league itself shrank from the earlier 12-slot/10-joined snapshot to an
  8-team, fully-joined league -- a real change on ESPN's side between
  fetches, not a bug) has a full real roster. Re-scrubbed and re-verified
  clean before committing: no secret key names, no un-pseudonymized
  identity GUIDs (the only GUID-shaped strings are the same class of CDN
  team-logo asset ids found clean in every prior fixture refresh), no
  leaked emails, member names properly pseudonymized.
- **Bug 1 -- `ingest.parse.parse_player_pool` only ever read
  `raw['_freeAgents']`.** That was the entire relevant player universe
  while the league was pre-draft, but ESPN removes a player from
  `_freeAgents` the moment they're drafted -- so once real rosters existed,
  every single starter on every team resolved to "no usable projection,"
  and `ingest.parse.build_team_params` (correctly, per its own contract)
  refused to fabricate a lineup and raised for every team. Fixed by adding
  `ingest.parse.every_player_with_stats`, which unions `_freeAgents` with
  every team's `roster.entries[].playerPoolEntry.player` (confirmed by
  direct inspection to carry an identical dict shape to a free-agent
  entry). `parse_player_pool` now sources from this union instead of
  `_freeAgents` alone. The same free-agent-only bug existed independently
  in `ingest.db.ingest_league`'s own raw-JSONB lookup (`raw_players_by_id`)
  -- fixed by reusing the same helper rather than writing a second version
  of the same union.
- **`scripts/ingest_synthetic_league.py` needed a matching fix**: its mock
  roster entries were bare `{"playerId", "lineupSlotId"}` dicts, since
  `parse_player_pool` never used to look inside any roster at all. Once it
  does, those entries needed a real `playerPoolEntry.player` block too (now
  built from `every_player_with_stats`, reused rather than duplicated) so
  the synthetic league stays genuinely ESPN-shaped and the fix above
  doesn't silently break the one thing it was built to validate.
- **Bug 2 -- `sim.api.roster_view.load_team_rosters` hard-failed an
  entire league over one bench player.** Real rosters can include a
  fringe bench player ESPN itself has no usable season projection for
  (found live: **James Conner**, real bench player on a real team, whose
  2026 season-total stat block has `appliedTotal == 0` -- the same "known
  bad player" pattern Phase 1 already documented, just now encountered
  rostered instead of as a free agent). The mock league never surfaced
  this because its draft only ever picks from already-projectable
  candidates. Resolved by distinguishing the two cases on purpose: an
  unprojected **starter** still hard-raises `MissingProjectionError`
  (exactly as load-bearing here as in `build_team_params` -- the
  simulation genuinely cannot proceed without it), but an unprojected
  **bench/IR** player is now rendered with a new `has_projection: bool`
  field and null numeric fields (`mean`/`sd`/`availability`/`floor`/
  `ceiling`) instead of blocking the whole team -- nothing in this module
  or `sim.engine` computes team strength from bench players, so there is
  nothing to fabricate a number for. Plumbed through
  `RosterPlayerOut`/`RosterPlayer` (TypeScript) end to end. The what-if
  `PlayerPicker` now disables selecting an unprojected player (with a "No
  ESPN projection available" tooltip) rather than letting the user submit
  a scenario the API would just 422 anyway; `TeamRiskCard` narrows
  `team.starters` to a guaranteed-projected type at render time via a
  small runtime-checked helper (`projectedStarter`), since starters are
  contractually never unprojected but the shared `RosterPlayer` type can't
  express that distinction statically. Two new backend regression tests
  cover both branches directly against the real fixture's real James
  Conner case, not a fabricated stand-in.
- Several docstrings/comments across `ingest/db.py`, `sim/params/*.py`,
  `sim/api/season_replay_view.py`, and `scripts/ingest_synthetic_league.py`
  asserted "this league is pre-draft" as a present-tense fact about
  `fixtures/league_raw_2026.json`. Reworded to past tense / historical
  framing (or, where the real point was about missing weekly game logs or
  missing actual scores rather than draft status specifically, reworded to
  state that actual claim precisely) rather than left stale for a future
  session to trust at face value.
- Several test assertions were hardcoded against the old 12-team/10-joined/
  296-player/6-playoff-team/84-matchup pre-draft snapshot; updated to the
  new 8-team/399-player/4-playoff-team/56-matchup/128-roster-row reality
  (`ingest/tests/test_parse.py`, `ingest/tests/test_db.py`). Two tests that
  specifically needed a *pre-draft* or *no-roster* scenario to test the
  right thing (`test_build_team_params_raises_when_no_roster_exists`,
  `test_precompute_all_leagues_skips_a_league_with_no_drafted_roster`) were
  converted to synthetic/derived payloads instead of relying on the real
  fixture happening to be in that state, so they keep covering that branch
  regardless of the real fixture's draft status going forward. A new
  positive-path test (`test_precompute_all_leagues_caches_the_real_drafted_league`)
  covers the case the old skip-test used to accidentally exercise.
  `test_synthetic_payload_parses_through_the_real_ingest_pipeline`'s
  `len(pool) == len(real_pool)` equality no longer holds now that "real
  free agent" and "real rostered" are both valid pool sources -- replaced
  with a subset check plus the actual regression that matters (every
  synthetic-team roster player resolves to a real projection).
- **Real league ingested and precomputed for real**: `ingest_league` run
  against the post-draft fixture (league_id=885686492, "Freakatron Gang
  2"), `sim.api.precompute` run to populate the real title-odds cache.
  Sanity-checked against PLAN.md's rough band (written for a 10-team
  league; this is 8 teams, so the even-odds baseline is 12.5% not 10%):
  best team 22.3%, worst 2.5%, sensible spread, no red flag. Verified
  visually in a real browser against the real running stack (API +
  dashboard, power rankings, roster risk, all three what-if tabs) --
  standings, rosters, matchups, and title odds are now the project owner's
  actual league, not a fabricated stand-in.
- `web/.env.local` (gitignored, local-only) `DEFAULT_LEAGUE_ID` switched
  from the SYNTHETIC league to the real one. `web/.env.example` (the
  committed setup template for a fresh clone with nothing ingested yet)
  deliberately left pointing at the synthetic id -- it's still the only
  league guaranteed to exist immediately after `scripts/ingest_synthetic_league.py`,
  and every page already works for any ingested league id, real or
  synthetic, with zero special-casing.
- The SYNTHETIC validation league and `scripts/ingest_synthetic_league.py`
  are kept, not removed -- still the fastest way to get a fully-drafted,
  fully-projectable league for local dev/testing without needing a real
  draft, and Phase 4/5b/6's tests still depend on it.

## Phase 7 — Draft Autopsy

- **Data-provenance investigation (the highest-risk part of this phase, done
  before writing any grading code), conclusion: the rank/ADP signal is an
  acceptable, honest proxy, not a hindsight leak.** Confirmed directly
  against the fixture: `draftDetail.completeDate` is 2026-08-05;
  `ownership.date` on sampled players (both free agents and rostered) reads
  ~2026-08-13, roughly 8 days later. Every `matchup.winner` in this league is
  still `"UNDECIDED"` (documented repeatedly in earlier phases) -- zero
  regular-season games have been played anywhere in this league's data, so
  this rank/ADP data cannot be contaminated by the specific failure mode
  PLAN.md warns against ("grade picks against players available at that
  pick, not against final season outcomes"). The 8-day gap can only reflect
  ordinary preseason drift (roster/depth-chart news, injury updates) --
  exactly the kind of noise a live draft-day ADP feed absorbs constantly,
  not a leak from games actually played. Concluded this is fine and
  documented the reasoning in `sim/api/draft_autopsy_view.py`'s module
  docstring rather than silently picking a rank source with no explanation.
- **Rank source: `draftRanksByRankType["PPR"]["rank"]`**, not `STANDARD` or
  `SUPERFLEX` (this league scores full PPR, established in Phase 1) and not
  the messier per-source `rankings` block (many `rankSourceId`s per player,
  built for positional expert-consensus display, not a single clean overall
  order). Verified directly: all 428 players this fixture carries stats for
  (300 free agents + 128 rostered, via `ingest.parse.every_player_with_stats`)
  have a distinct, positive PPR rank -- zero missing, zero duplicates, zero
  zero/null ranks -- so it is a genuine total order with no gaps to paper
  over. `ownership.averageDraftPosition` is carried alongside on every graded
  pick as `player_adp`, denominated in the same units as
  `overallPickNumber`, purely as a secondary, display-only cross-check --
  never used to decide a grade, an alternative, or a best/worst pick, so the
  entire grading methodology rests on one documented, auditable source.
- **"Value gap" (the core per-pick metric) is same-position, not
  positionless.** For every pick, the alternative is the best-ranked player
  at the *same* `defaultPositionId` label who was still undrafted at that
  exact moment (excluding the player actually taken) -- not literally "the
  single best player left on the board at any position." A positionless
  comparison was considered and rejected: with 8 teams each required to
  start exactly 1 QB/1 K/1 D-ST, a mandatory late positional fill would
  almost always compare unfavorably against whatever RB/WR happened to be
  sitting on the board at that pick, which would be a misleading "reach"
  signal for a pick that was structurally necessary, not a mistake. Same-
  position comparison answers a fair, actionable question instead: "given
  you took this position here, did you take the best one available." The
  literal best-available-anywhere player is still recorded on every pick
  (`best_overall_available_*`) as informational context, just never used to
  drive a grade.
- **`value_gap = alternative_player_rank - player_rank` can be negative**,
  and that is the intended, informative case (a better-ranked player at the
  same position was passed over -- a reach), not a bug: an earlier draft of
  this reasoning assumed the pool's best-remaining player is always *worse*
  than what was taken, which is only true if every manager always drafts
  in strict rank order -- exactly the behavior being graded, not something
  to assume. Verified live against the real draft: pick #1 (Bijan Robinson,
  rank 2) shows a small negative gap against Jahmyr Gibbs (rank 1, taken
  next at pick #2) -- correctly flagged as a marginal, real reach rather
  than silently treated as impossible.
- **`grade_bucket` (QB/RB/WR/TE/Bench, PLAN.md's literal five categories)
  is driven by the pick's own `lineupSlotId` at draft time** (0/2/4/6/20,
  with FLEX (23) resolved to the taken player's own position), not by
  where that player sits on the *current* roster. Verified this distinction
  is load-bearing, not theoretical: cross-referencing all 128 picks against
  today's rosters found 6 team mismatches (trades) and 4 picked players no
  longer on any roster (waived, one case being a swapped D/ST placeholder
  ID), all real roster churn between the 2026-08-05 draft and the fixture's
  ~2026-08-13 fetch. Grading must reflect the pick that was actually made,
  not later transaction activity a draft-grade has nothing to do with. K and
  D/ST picks are graded per-pick (real `value_gap`, shown in the full draft
  board) but excluded from the bucket summary, matching PLAN.md's literal
  five-category list rather than inventing a sixth/seventh bucket it didn't
  ask for.
- **Best/worst decision are both driven by the same `value_gap` extremes**
  (max for best, min for worst) -- deliberately not a blended score that
  also folds in ADP-relative "steal" framing (`player_adp - overall_pick_number`,
  a different, real, and initially-considered metric). PLAN.md's own phrase
  --"the specific alternative that was on the board at that pick" -- demands
  a named substitutable player attached to *both* the best and worst
  callout, which only the same-position `value_gap` concept naturally
  produces; an ADP-vs-pick-number score has no "alternative player" to name.
  Combining the two into one composite number would need an arbitrary
  relative weighting between "rank-spots" and "pick-number-spots" that isn't
  fitted from anything -- exactly the class of invented constant CLAUDE.md's
  "no invented numbers" rule warns against, even though this feature sits
  entirely outside `simulate_seasons()`. Kept as two separate, individually
  honest numbers instead: `value_gap` drives best/worst; `player_adp` is
  supplementary context shown per pick.
- **The structural finding is synthesized from real per-position facts, not
  a template.** For each team and each of QB/RB/WR/TE (by the player's own
  position label, bench picks included -- deliberately a different
  aggregation than `grade_bucket`, since "RB depth" has to include bench
  RBs to mean anything), `_synthesize_structural_finding` compares the
  team's own first-pick-number and average `value_gap` at that position
  against the league-wide average at the same position. A "you waited too
  long" causal sentence is only produced when a position clears *both* an
  explicit lateness threshold (>6 picks later than the league average first
  pick) *and* a value threshold (>3 rank-spots worse than league average) --
  requiring both signals together, not just one, so the sentence only fires
  when timing plausibly explains the value lost, matching PLAN.md's own
  example ("you waited too long on RB depth, which forced low-upside
  options later") rather than making a causal claim off a single stat. A
  second sentence names the team's best relative-value position for
  balance. When no position clears both thresholds, a different, honest
  fallback sentence fires instead (either naming the single weakest
  position without a causal timing claim, or reporting that nothing stands
  out) -- verified live against all 8 real teams: outputs ranged from
  genuine "waited too long on WR/RB/TE" causal narratives to two teams
  ("Milking the McCaffinator", the unnamed team `.`) whose picks were close
  enough to league norms that the fallback "no single position stands out"
  sentence fired correctly, confirming the logic doesn't force a dramatic
  story where the data doesn't support one.
- **`_GRADE_LABEL_THRESHOLD` / `_LATE_TIMING_THRESHOLD` / `_GAP_CAUSE_THRESHOLD`
  are editorial thresholds for choosing display language over an
  already-computed number**, the same class of choice as the existing
  `TeamRiskCard.riskBand()` cutoffs (0.05 / 0.15) or `roster_view.py`'s
  10th/90th floor/ceiling percentile choice -- not a fitted or invented
  input to `simulate_seasons()`, and documented as such directly in
  `sim/api/draft_autopsy_view.py` rather than left as unexplained magic
  numbers.
- **Backend module deliberately not vectorized with NumPy.** This is
  discrete bookkeeping over a fixed, already-happened 128-pick sequence
  (~55k worst-case comparisons), not a Monte Carlo simulation --
  CLAUDE.md's "vectorize" rule targets `sim.engine`'s per-simulation arrays;
  applying it here would obscure straightforward pick-by-pick logic for no
  performance benefit at this scale. `sim/engine.py` and
  `sim/tests/test_engine.py` were not touched by this phase.
- **`compute_draft_autopsy` is split into a thin Postgres-loading wrapper and
  a pure `_compute_from_raw(raw, league_id, season_id)`** so the grading
  logic itself (10 of the phase's 15 new tests) can be exercised directly
  against the in-memory fixture dict with no Postgres dependency, while a
  separate small set of Postgres-backed tests covers the route and
  `load_raw_payload` wiring -- mirroring the fast-unit-tests-plus-thin-
  integration-tests split already implicit elsewhere in `sim/api`.
- **New errors added to `ingest/errors.py`**: `DraftNotAvailableError`
  (no `draftDetail.picks` to grade -- covers both a genuinely pre-draft
  league and the SYNTHETIC validation league's picks-less mock draft) and
  `MissingRankDataError` (a player needed for grading has no usable
  `draftRanksByRankType.PPR.rank`). Both are `IngestError` subclasses, so
  they fall through `sim/api/app.py`'s existing `_DATA_UNAVAILABLE_ERRORS`
  → HTTP 409 mapping with zero changes to that tuple.
- **UI: per-team report cards (structural finding → best/worst → positional
  grades) lead the page; one shared full-league draft board table follows,
  once, below all 8 cards** -- rather than repeating a 16-row pick table
  inside every team card. This is the concrete implementation of PLAN.md's
  "structural narrative given more weight... not buried below a wall of
  per-pick data": the narrative is the first, largest element in every
  card, and the exhaustive per-pick view exists exactly once, after every
  team's synthesis has already been read. Matches the whole-league-at-once
  pattern every other page in this app already uses (Roster Risk, Power
  Rankings) rather than introducing a new team-selector/tabs pattern this
  app has never used.
- **Draft board grid values are always visible directly on each cell**
  (player name, position/slot, pick number, signed value_gap), never
  hover-only -- per MASTER.md's own chart accessibility guidance ("value
  labels always visible on each bar, not hover-only"). No tooltip-based
  interactivity was added to the grid for this reason, and because
  hover-only content degrades badly on the touch-only mobile layout this
  app is explicitly built for.
- **Verification**: `pytest -q` 138 passed (128 from Phases 0-6 plus 10 new
  in `sim/tests/test_api_draft_autopsy.py`); `mypy --strict sim ingest` 21
  pre-existing errors (0 new); `ruff check sim ingest db scripts` shows only
  the same pre-existing findings in files this phase didn't touch (0 new).
  Web: `tsc --noEmit`, `eslint .`, and `npm run build` (production) all
  clean. Visually verified in a real browser (uvicorn + Next.js dev server,
  both against the real league, `league_id=885686492`) at 375px mobile and
  1440px desktop: all 8 teams' structural findings, best/worst decision
  callouts (with the named alternative and signed value gap), positional
  grade strips, and the full 128-pick draft board grid render correctly;
  confirmed `document.documentElement.scrollWidth === window.innerWidth` at
  375px (no page-level horizontal scroll) while the draft board table's own
  wrapper legitimately scrolls horizontally within itself
  (`scrollWidth: 1403px` vs `clientWidth: 311px`); confirmed the SYNTHETIC
  league (`league_id=-1990001`) renders a clean, readable 409 panel instead
  of a crash or blank page, since its mock draft has no pick sequence to
  grade.

## Phase 8 — Playoff Planner

- **"Run the simulator restricted to the league's own playoff weeks" is an output restriction, not
  an input restriction.** Which two teams occupy a bracket seed slot is itself an output of the
  regular season (`SimulationResult.seed_rank`), so playoff weeks cannot be simulated in isolation
  from the regular season that determines who's in them -- and `raw["schedule"]` has zero playoff
  entries anyway (ESPN doesn't publish playoff pairings until real seeding exists, confirmed
  directly against the fixture). `simulate_seasons()` is called completely unmodified (full
  regular season + `n_playoff_rounds`, exactly as every other feature calls it); this phase's
  module only *reports* on the playoff-relevant slice of what it already returns/samples. Full
  reasoning lives in `sim/api/playoff_planner_view.py`'s module docstring.
- **The "strength of schedule" data-gap question, resolved as the task laid out: fantasy-opponent
  strength, not real NFL defensive strength.** This fixture has no real NFL opponent/game-schedule
  data anywhere (only `proTeamId`), so a literal "which NFL defense do you face in week 16" reading
  is not buildable without inventing data and was not attempted. What's built instead: each team's
  own roster strength, position by position, for the playoff-length window, ranked against the rest
  of the league (`SlotPlayoffStrength.league_rank`/`league_percentile`) -- "how does my RB corps
  compare to the field of realistic bracket opponents for these specific weeks," which is fully
  derivable from data this codebase already has. Not attempted: a per-matchup "vs. your specific
  projected round-1 opponent" comparison -- with 4 playoff teams there are only 2 possible round-1
  pairings and the field-wide percentile already answers the more useful question ("who in this
  league should I actually worry about at this position") without conditioning on an opponent
  assignment that's itself uncertain.
- **`sim.engine.bracket_pairings()` added and `_run_playoffs` refactored to call it**, extracting
  the exact 1v4/2v3-style array-slice pairing rule (`high = playing[:, :m//2]`, `low =
  reversed(playing[:, m//2:])`) that already existed inline, so the Playoff Planner's projected
  bracket reuses the identical rule rather than re-deriving it -- not a second bracket-logic path.
  Provably behavior-preserving (same index arithmetic, no rng involved): all 25 golden tests in
  `sim/tests/test_engine.py` pass unchanged after the refactor, checked immediately, before writing
  anything else.
- **Real league setting note, left as-is on purpose:** this league's `playoffSeedingRule` is
  `TOTAL_POINTS_SCORED` and `playoffReseed` is `false`, per the task's own DATA FACTS.
  `simulate_seasons()`'s existing `standing_key = wins + 0.5*ties, tiebroken by points_for` already
  matches `TOTAL_POINTS_SCORED` as a *tiebreaker* rule (not a primary-sort override), and
  `_run_playoffs` always reseeds every round regardless of `playoffReseed`. Checked whether this
  reseed-always behavior actually changes anything for *this* league: with exactly 2 rounds (4
  playoff teams), reseeding only reorders which finalist is labeled "high" vs "low" going into the
  championship, and the win/loss comparison (`high_pts > low_pts`) is symmetric in that labeling --
  so it provably cannot change any simulated outcome here. Left `_run_playoffs` untouched rather
  than adding reseed-toggle logic to the one shared simulation engine for a flag that is a no-op at
  this league's current bracket size; worth a real fix only if/when a league with >2 playoff rounds
  is ever simulated.
- **Projected bracket: single most-probable seed assignment via `scipy.optimize.linear_sum_assignment`
  over the (team x seed) probability matrix implied by `seed_rank`**, maximizing total assigned
  probability -- the same optimization technique Phase 6's `season_replay_view` already established
  for a structurally identical "best assignment of items to slots" problem (there: players to
  lineup slots; here: teams to bracket seeds), not a new pattern. Only round 1 is named with
  specific teams; a later round (the final, for this league) is deliberately shown as "winner of
  Matchup N vs. winner of Matchup M" with no named teams, since which two round-1 winners actually
  meet is itself stochastic and naming a pair would fabricate certainty the projection doesn't have.
- **First design of `floor_ratio_delta` (regular-season floor ratio minus playoff-window floor
  ratio) was NOT team-specific, and this was caught before shipping it as the weakness signal.**
  `sim.params` fits one coefficient-of-variation per *position*, shared league-wide (Phase 2); the
  Gamma shape parameter driving a floor/mean ratio is `(mean/sd)^2 = 1/CV^2`, independent of any
  individual player's own mean. Verified live: every team's regular-season floor ratio at a given
  slot label landed within ~0.001 of every other team's, and `floor_ratio_delta` cleared the
  editorial 0.08 threshold for nearly every slot on every team (a real, but purely structural,
  "short windows amplify volatility" fact, not a team-differentiated one). Shipping that alone as
  "your weakness" would have recommended the same slot (whichever position has the smallest
  starting group, e.g. FLEX) to literally every team in the league -- exactly the "generic template,
  not real advice" failure PLAN.md explicitly warns against.
- **Fix: `is_playoff_specific_weakness` requires `floor_ratio_delta` clearing the threshold AND
  zero same-position bench depth**, the real per-team-differentiating signal -- computed the same
  way `sim.api.roster_view`'s existing `positional_concentration` already does (count bench/IR
  entries by their own `defaultPositionId`, not the literal slot label, so a bench RB backs up a
  FLEX-RB starter too). This is the same "multiple signals together before a causal claim"
  discipline `draft_autopsy_view._synthesize_structural_finding` already established. Confirmed
  live this is genuinely team-differentiating where the raw delta wasn't: e.g. of the real league's
  8 teams, 6 get "target D/ST" (only 2 of 8 carry a bench D/ST at all -- a real, honest fact about
  how this league drafts, not noise), 1 gets "target TE" (the one team with zero bench TE), 1 gets
  "target K" (no team in this league carries a bench K, so K's lower-but-still-real delta becomes
  the deciding factor for whichever team has no other qualifying gap).
- **Recommendation text is synthesized per team from real numbers** (`_synthesize_recommendation`
  in `sim/api/playoff_planner_view.py`), the same "server-computed narrative, honest fallback when
  nothing clears the bar" pattern `draft_autopsy_view` established -- never a template filled with
  just the team name, and an explicit different sentence when no slot combines both a real delta and
  zero bench depth ("no real playoff-specific weakness here").
- **Live-computed, not cached, like `/roster`/`/schedule`/`/draft-autopsy` -- but deterministically
  seeded** via `sim.api.seeds.precompute_seed(league_id, season_id)`, the exact formula the cached
  `/simulation` endpoint already uses, reused rather than drawing a new one -- consistent numbers
  for the same league/season across endpoints, and a single `rng` instance consumed sequentially by
  `simulate_seasons()` then the per-slot `_sample_player_weeks` call, so the whole response is
  reproducible end to end. `n_sims=10,000` (`DEFAULT_N_SIMS`), matching `PRECOMPUTE_N_SIMS`'s own
  reasoning -- no waiting user for this route the way a live what-if has, so there's no reason to use
  a smaller count. Full computation (regular+playoff simulation plus the per-team per-slot sampling
  for 8 teams) runs in well under a second against the real league.
- **Unlike Draft Autopsy, the SYNTHETIC validation league (`league_id=-1990001`) works fine here**,
  confirmed with a dedicated test (`test_synthetic_league_also_produces_a_full_playoff_plan`) and
  live against the running API: its settings (schedule length, playoff team count) are copied
  verbatim from the real league by `scripts/ingest_synthetic_league.py`, and it has a real, fully
  drafted roster (just no real pick *sequence*, which is what draft autopsy specifically needs and
  this feature doesn't). One real consequence worth noting: the synthetic league's mock draft never
  fills bench slots (Phase 5b), so every slot for every synthetic team shows zero bench depth --
  an honest, if extreme, reflection of that fixture's real (if synthetic) roster shape, not a bug.
- **UI layout mirrors Draft Autopsy's established precedent exactly**: per-team cards lead the page
  (`TeamPlayoffCard`), each one leading with `RecommendationCallout` -- a distinct amber/brand-accent
  treatment (not the primary-blue `StructuralFindingCard` styling) so it visually reads as an action,
  not a finding -- followed by the per-slot strength breakdown (weakest slot shown first). One
  shared `BracketPanel` + `SeedingOddsTable` follow below all 8 cards, once, matching "one shared
  board after every team's own narrative" rather than repeating league-wide data inside every card.
- **`SlotStrengthBar` shows two floor-ratio bars (full season vs. playoffs) per slot rather than a
  bare delta number** -- MASTER.md's "distributions, not point estimates" principle applied to this
  specific claim: the whole point is that the floor moves between two windows, which reads far more
  legibly as two comparable bars than as a single subtracted number. `SeedProbabilityStrip` reuses
  `FinishDistributionStrip`'s exact segmented-bar pattern (already MASTER.md's recommended shape for
  a discretized probability mass function) for `seed_probabilities` instead of `finish_distribution`.
- **Verification**: `pytest -q` 156 passed (138 from Phases 0-7 plus 18 new in
  `sim/tests/test_api_playoff_planner.py`); `mypy --strict sim ingest` 21 pre-existing errors (0
  new); `ruff check sim ingest db scripts` shows only the same pre-existing findings in files this
  phase didn't touch (0 new in `sim/engine.py`, `sim/api/playoff_planner_view.py`,
  `sim/api/app.py`, or the new test file). Web: `tsc --noEmit`, `eslint .`, and `npm run build`
  (production) all clean. Visually verified in a real browser (uvicorn + Next.js dev server, both
  against the real league, `league_id=885686492`) at 375px mobile and 1440px desktop: all 8 teams'
  "Do this now" recommendations, per-slot floor-ratio bars (color-flagged red only for a genuine
  bench-depth-driven weakness), the projected 1v4/2v3 bracket, and the league-wide seeding-odds
  table with seed-probability strips all render with correct real data; confirmed no page-level
  horizontal scroll at 375px (`scrollWidth === innerWidth`) and no console errors at either width.
  One verification wrinkle, consistent with a known prior-phase tooling issue: screenshots taken
  after scrolling deep down this page intermittently rendered blank in this browser tool regardless
  of tab freshness or scroll method (raw scroll, keyboard `End`, element `scroll_to` all reproduced
  it) -- cross-checked directly against the real DOM instead (`get_page_text` and the accessibility
  tree via `read_page`, both queried against the scrolled-to content) to confirm the bracket panel
  and seeding table render correct real data; zero console errors either way. Treated as the same
  screenshot-capture tool artifact flagged before this phase started, not an app bug -- verified via
  DOM content directly rather than assumed.

## Phase 9a — Lineup optimizer

*Covers Phase 9's "Lineup optimizer" sub-feature (feature 10) only -- Waiver intelligence and Weekly
recap are separate future sessions, per PLAN.md's "three separate sessions" instruction.*

- **Season-long-override framing, argued explicitly rather than silently assumed.** Read
  `sim/engine.py` line by line before writing any code to confirm `simulate_seasons()`'s
  `roster_overrides` replaces a team's `starters` for the entire simulated season -- a team's
  `TeamParams` is rebuilt once per call and `_sample_team_weeks` samples every `n_weeks + n_rounds`
  column from that one fixed tuple; there is no per-week lineup mechanism anywhere in the engine (its
  own module docstring says so: "v1 models a fixed starting lineup per team for the whole season...
  a deliberate simplification, not an oversight"). Framing "safest"/"highest upside" as season-long
  choices is therefore not a shortcut this phase took -- it is the only framing the engine can express
  today. It also happens to match this league's actual state: every matchup for league_id=885686492 is
  still `UNDECIDED` (repeated across Phases 5-8), so there is currently no real distinction between
  "this week" and "the rest of the season" to collapse. Documented in `sim/api/lineup_optimizer_view.py`'s
  module docstring, and surfaced directly in the UI (`components/lineup/framing-note.tsx`), not just
  here, that this will need revisiting once real weeks are played -- a future phase's territory.
- **Floor computed from real Monte Carlo samples of the team TOTAL, never a sum of individual player
  floors.** The 10th percentile of a sum of independent random variables is not the sum of their
  individual 10th percentiles -- summing floors overstates real downside risk by implicitly assuming
  every player has a bad week simultaneously. `_weekly_totals` draws
  `sim.engine._sample_player_weeks(candidate_starters, n_sims=20,000, n_weeks=1, rng)` (the literal
  per-player sampling primitive `simulate_seasons()` itself calls) and sums across players *before*
  taking any percentile. No second sampling implementation anywhere -- same reuse pattern
  `sim.api.season_replay_view` and `sim.api.playoff_planner_view` already established for this exact
  primitive.
- **Search space: the current lineup plus every single-slot swap, not a full permutation.** Measured
  directly against the real league before writing the search: the full Cartesian product of legal
  per-slot-instance fillers (9 starting slots: 1 QB/2 RB/2 WR/1 TE/1 FLEX/1 D-ST/1 K, 6-7 bench players
  per team) ranges from ~1,300 to ~4,000 candidate lineups per team -- intractable to re-run
  `simulate_seasons()` against for the highest-upside search. Every single-slot swap (one starting-slot
  instance's occupant replaced by one real, `eligibleSlots`-eligible bench/IR player, every other slot
  held at its current occupant) collapses this to 14-19 candidates per team -- measured ~2s for 19
  candidates x `simulate_seasons(n_sims=5000)` against the real 8-team league. This is exactly PLAN.md's
  own suggested framing ("FLEX slot choice, any position where a bench player is a legitimate
  alternative to a starter"), not an invented shortcut: it is literally "which single bench player, if
  any, is a real alternative to a specific starter," the actual decision a manager faces one slot at a
  time. Deliberately does not search compound multi-slot swaps (e.g. changing FLEX and RB2
  simultaneously) -- that reintroduces the same combinatorial blowup the single-swap scoping exists to
  avoid. Verified live against the real league (`sim/tests/test_api_lineup_optimizer.py`): every
  generated candidate differs from the baseline in exactly one slot, and the eventual "safest"/"highest
  upside" pick is always either the baseline itself or one of these single-swap candidates -- both
  observed live (team 6, "Olave Garden," picks two *different* single swaps: TE Tucker Kraft for
  safest, FLEX Travis Etienne Jr. for highest upside -- a genuine three-way tradeoff; three of the real
  league's 8 teams have no single swap that improves on the current lineup for either objective, an
  honest result, not a bug).
- **"Highest upside" search reuses `simulate_seasons()` directly via `roster_overrides`, once per
  candidate, comparing `won_title`** -- never mean points, never single-week upside, per PLAN.md's
  explicit instruction. Every other team in the league keeps its real ingested roster during this
  search (`roster_overrides` only touches the one team_id under evaluation), so a candidate's title
  probability reflects a change to this team alone, not a hypothetical change to the whole league.
  `n_sims=5,000` per candidate (`DEFAULT_SEASON_N_SIMS`) -- higher than the live what-if default
  (2,000) because this module ranks up to ~19 similar candidates against each other and a probability
  that's close between two lineups needs less sampling noise in the ranking than a single before/after
  comparison does; still fast (~2s total for 19 candidates against the real league).
- **New deterministic seed: `sim.api.seeds.lineup_optimizer_seed(league_id, season_id, team_id)`**,
  folding `team_id` into the existing `precompute_seed` formula so two different teams' searches in
  the same league/season don't consume the identical draw sequence, while staying live-computed (not
  cached) and reproducible like `/roster`/`/schedule`/`/playoff-planner`. Each candidate is fully
  evaluated (weekly totals, then season outcome) before moving to the next, in a fixed, documented
  order, so a given seed always reproduces the same result -- verified directly
  (`test_result_is_deterministic_for_a_fixed_seed`).
- **New route: `GET /league/{id}/lineup-optimizer/{team_id}`**, following every established pattern
  from prior phases exactly: `resolve_season_id` for the optional season, `LeagueNotIngestedError`/
  `UnknownTeamError` -> 404, `IngestError`/`ParamsError` subclasses -> 409, a thin
  `_compute_from_raw`/`compute_lineup_optimizer` split (`sim/api/lineup_optimizer_view.py`) so grading
  logic has fast, Postgres-free unit tests mirroring `sim.api.draft_autopsy_view` /
  `sim.api.playoff_planner_view`. `UnknownTeamError` is a new, narrow `ValueError` subclass (not an
  `IngestError`) since it's not a data-availability problem -- it's a request for a team_id that was
  never real for this league/season, the same class of "this specific thing was never real" case
  `LeagueNotIngestedError` covers for the league itself.
- **UI: three cards side by side, tradeoff made explicit as its own headline element, not left for the
  reader to infer from two lists of names.** `lib/lineup-optimizer.ts::describeLineupTradeoff` is pure
  arithmetic over the three already-returned `LineupProjection`s (floor delta, title-probability delta)
  -- the same "natural-language sentence built from already-computed deltas, kept in `lib/`, not a
  component" pattern `lib/whatif-compare.ts::describeTradeAsymmetry` already established in Phase 6, so
  this isn't new analytics logic in the CLAUDE.md sense. Handles four distinct real cases observed live
  against the real league: safest and upside are the literal same candidate (team 2's D/ST swap helps
  both objectives at once); neither objective has a real single-swap improvement (3 of 8 teams); only
  one objective improves; and both improve via genuinely different swaps (team 6). `RangeBar` (Phase
  5b's roster-risk component) is reused as-is for each lineup's weekly floor/mean/ceiling, sharing one
  `scaleMax` across all three cards in a row so bar lengths are directly comparable card to card, per
  that component's own existing convention. Every roster row that differs from Current is highlighted
  (amber background, bold name, swap icon) using the API's own `is_swap` field -- the frontend never
  re-derives which slot changed by diffing player ids itself.
- **Team switcher is a server-navigated `?team=` link row (`components/lineup/team-picker.tsx`), not
  client-side `useState` like the What-If page's scenario builders.** This page's computation is a
  genuine live search (weekly MC sampling plus up to ~19 fresh `simulate_seasons()` calls per team),
  not a cheap read -- matches this app's established pattern of URL-driven Server Component pages
  (dashboard, power rankings, playoffs) rather than What-If's in-place client scenario runs, and gets a
  real Next.js `loading.tsx` skeleton for free on every team switch.
- **Verification**: `pytest -q` 171 passed (156 from Phases 0-8 plus 15 new in
  `sim/tests/test_api_lineup_optimizer.py`); `mypy --strict sim ingest` 21 pre-existing errors (0 new);
  `ruff check sim ingest db scripts` shows only the same pre-existing findings in files this phase
  didn't touch (0 new in any Phase 9a file). Web: `tsc --noEmit`, `eslint .`, and `npm run build`
  (production) all clean. Visually verified in a real browser (uvicorn + Next.js dev server, both
  against the real league, `league_id=885686492`) at 375px mobile and 1440px desktop: team switcher,
  framing note, tradeoff headline, and all three lineup cards (weekly range bar, title/playoff odds,
  finish-distribution strip, full roster with swap highlighting) render correct real data for multiple
  teams, including a genuine three-way tradeoff (team 6) and a no-improvement-available case (team 1);
  confirmed no page-level horizontal scroll at 375px (`scrollWidth === innerWidth`, checked both before
  and after scrolling) and zero console errors. One verification wrinkle, the same class already
  flagged in Phase 8: `computer` screenshot/scroll calls intermittently hung or returned a blank image
  partway down this page in this browser tool; cross-checked with `get_page_text` first (itself
  sometimes stale immediately after a fresh navigation) and, when that also looked incomplete, with
  direct `document.body.textContent` reads via the JS tool, which consistently showed the full, correct
  real content every time -- confirmed a tooling artifact of this session's browser pane, not an app
  bug, the same conclusion Phase 8 reached under the same symptom.

## Phase 9b — Waiver intelligence

*Covers Phase 9's "Waiver intelligence" sub-feature (feature 9) only -- Lineup optimizer was Phase 9a;
Weekly recap is a separate future session, per PLAN.md's "three separate sessions" instruction.*

- **`_freeAgents` used directly, no widening needed -- the one Phase-9 sub-feature where that's true.**
  Confirmed live: `raw["_freeAgents"]` already excludes every drafted player in this league (unlike the
  pre-draft era, where it was the entire player universe -- see "No more mock data"), so it means exactly
  "actually available to add" with zero extra filtering. Every other phase since the real draft needed
  `ingest.parse.every_player_with_stats` (the free-agent/rostered union) because it needed projections for
  players who are NOW rostered; this feature is the opposite case and doesn't need that union at all.
- **Data-provenance investigation, same honesty discipline Phase 7 established for `draftRanksByRankType`:**
  `ownership.percentOwned`/`percentStarted`/`percentChange`/`averageDraftPosition` and `injuryStatus` are
  ESPN's own **cross-league** consensus data (aggregated across many ESPN leagues), not specific to this
  league's own manager behavior or transaction history (this fixture has no waiver-claim/add-drop log for
  any league). Documented explicitly in `sim/api/waiver_intelligence_view.py`'s module docstring, in the
  API response (`ownership_data_note`), and in the UI itself (`OwnershipNote`) -- not just in this file.
  Two candidate fields were investigated and found **unusable**, so not used anywhere: `ownership.activityLevel`
  is `null` for all 300 real free agents (verified directly), and `player.active` is `True` for all 300 free
  agents and all 128 rostered players alike -- both constants carry zero information, the same "a constant
  field is indistinguishable from an invented one" reasoning that has recurred since Phase 1.
- **Signal 1, Opportunity: `opportunity_score = 0.0` if `injuryStatus` is `OUT`/`INJURY_RESERVE`, else
  `clip(percent_started / percent_owned, 0, 1)`.** The ratio -- among ESPN teams that already own this
  player, what fraction actually start him -- is a real, directly-computed role-trust signal, verified live
  to behave sensibly: K/D-ST (single starting slot, almost never handcuffed) cluster near 1.0 (Cam Little
  0.91, Harrison Butker 0.88), while RB/WR/QB free agents cluster far lower even at high ownership (Patrick
  Mahomes: 85% owned, 18% started, QUESTIONABLE at fetch time; Alvin Kamara: 50% owned, 2% started, ADP
  156.7 -- a late-round handcuff stash). The `OUT`/`INJURY_RESERVE` override forces the score to the
  natural floor of its own `[0,1]` range (a factual "not playing" statement) rather than blending in an
  invented penalty coefficient; `QUESTIONABLE` deliberately does **not** override, since the ratio itself
  already reflects real managers discounting a questionable player -- an extra invented penalty on top
  would double-count real information with a made-up number. `averageDraftPosition` relative to this
  player's own projection rank was investigated per PLAN.md's own suggestion and **deliberately rejected**
  for Opportunity: that gap measures market-vs-model *value* disagreement, not role/usage certainty --
  exactly the "already a good player" conflation PLAN.md's instruction warns against. Kept as a separate,
  honest, display-only `average_draft_position` field instead (same treatment Phase 7 gave `player_adp`).
- **Signal 2, Availability: `percent_owned` used as-is, no inversion.** Deliberately not a directional
  "good/bad" score -- a high `percent_owned` free agent still on this league's wire is the more surprising,
  often more valuable fact (a widely-valued player this league's managers haven't grabbed), the opposite of
  "low ownership = hidden gem." It drives the reasoning text's framing, never the ranking itself.
- **Signals 3 & 4, League fit / Competition: `sim.api.roster_view`'s `positional_concentration` /
  `sim.api.playoff_planner_view`'s `bench_position_counts` RULE reused, reimplemented locally** (count a
  team's own bench/IR entries by real `defaultPositionId` label) for the same reason `playoff_planner_view`'s
  own docstring gives for the identical situation: the rule is shared, the local dataclass shape isn't.
  League fit = the requesting team's own bench depth at a position; Competition = the identical rule applied
  to every other team, naming rivals with zero depth (per PLAN.md's literal "which teams likely target him,"
  not just a count).
- **Caught before shipping (design flaw #1): K/D-ST bench depth is a near-constant, not a real per-team
  signal.** Verified directly: 0 of this league's 8 real teams carry *any* bench K; only 2 of 8 carry any
  bench D/ST. Using "zero bench depth" as a "need" signal for these two positions the same way as QB/RB/WR/TE
  would make it trivially `True` for literally every team -- not team-differentiating, the same "verify this
  is actually real signal before shipping it" discipline Phase 8 applied to `floor_ratio_delta`. Fix:
  `_BENCH_DEPTH_RELEVANT_POSITIONS = {QB, RB, WR, TE}` (the same four positions `draft_autopsy_view` already
  treats as worth a positional grade) gates Signals 3/4; K/D-ST get `team_has_positional_need=False`,
  `rival_teams_with_need=()` always, plus an honest sentence explaining bench depth isn't a real decision at
  those two positions in this league, rather than a fabricated need/no-need claim.
- **Caught before shipping (design flaw #2, the bigger one): a first flat cross-position ranking was
  dominated top-to-bottom by kickers for every team.** Root cause was structural, not a bug in one number:
  `opportunity_score` is a *within-position* trust ratio, and K/D-ST structurally have almost no bench
  competition at their own position, so they cluster near 0.9+ while RB/WR free agents (mostly
  backups/committee pieces by definition) cluster much lower -- multiplied by a comparable per-game mean
  (a solid K and a low-end skill free agent often both project 7-9 pts/game), `expected_playable_points`
  was never an apples-to-apples number *across* positions. Fix: the response is grouped by position
  (`WaiverPositionGroup`), `expected_playable_points` only ranks candidates *within* a group, and groups
  themselves are ordered (real positional need first, in canonical QB/RB/WR/TE/D-ST/K order; K/D-ST last,
  explicitly framed as streaming options). This also let League fit/Competition (genuinely position-level
  facts) be stated once per group instead of being repeated verbatim on every candidate row, which the first
  draft also did.
- **Ranking is a lexicographic sort within a group (`expected_playable_points` descending), never a single
  blended composite with invented weights** -- the same "no arbitrary relative weighting between two
  real-but-differently-scaled numbers" discipline `draft_autopsy_view` established when it explicitly
  rejected blending `value_gap` and an ADP-relative "steal" metric. `expected_playable_points =
  mean_points_per_game * opportunity_score` is the exact same "mean times a `[0,1]` probability-like ratio"
  pattern `roster_view._team_risk_rating` already uses (`sum(mean_i * availability_i)`), just applied to one
  candidate instead of summed across a roster -- not a new kind of number.
- **`simulate_seasons()` is not called anywhere in this module -- a deliberate scope decision, argued
  explicitly rather than silently skipped.** A real extension was on the table: "how much would adding this
  player change my title odds," via the exact same `roster_overrides` mechanism `lineup_optimizer_view`
  already uses. `lineup_optimizer_view` makes that tractable by scoping to ~14-19 candidates for ONE
  already-rostered team; waiver intelligence's candidate pool is up to 300 free agents, and a meaningful
  "impact if added" number needs both a roster-construction decision (who gets replaced) AND a
  per-candidate `simulate_seasons()` call -- either re-deriving `lineup_optimizer_view`'s entire search 300
  times, or arbitrarily inventing which bench slot gets replaced. Kept simple per PLAN.md's own bare-minimum
  ask; documented as a real, legitimate future extension in the module docstring, not dismissed. No RNG
  anywhere in this module, unlike every other `sim.api` view module -- there's nothing stochastic to seed.
- **New route: `GET /league/{id}/waiver-intelligence/{team_id}`**, following every established pattern:
  `resolve_season_id` for the optional season, a local `UnknownTeamError` (`ValueError` subclass, same
  "this specific thing was never real" class `LeagueNotIngestedError` covers for the league itself, mirrored
  from -- not imported from -- `lineup_optimizer_view` to keep each view module self-contained like its
  siblings) -> 404, `IngestError`/`ParamsError` subclasses -> 409, and a thin `_compute_from_raw`/
  `compute_waiver_intelligence` split for fast Postgres-free unit tests. New `limit_per_position` query
  param (default 8, an editorial UI-sizing choice, the same class as `roster_view`'s 10th/90th percentile
  band) replaces a flat `limit` once the response became position-grouped.
- **UI: `PositionGroupCard` leads with the group's League fit/Competition narrative as a headline callout**
  (amber/brand-accent when a real need, quieter primary tone for "depth add," muted for K/D-ST "streaming
  options") -- the same narrative-leads-data-follows layout Draft Autopsy's `StructuralFindingCard` and
  Playoff Planner's `RecommendationCallout` already established. Each `CandidateRow` underneath leads with
  its own server-synthesized Opportunity/Availability reasoning sentence, then supporting chips (opportunity
  %, ownership %, projected pts/gm, ADP); rank number and `expected_playable_points` (labeled "realistic
  weekly value") anchor the row. Together the group header and each row satisfy PLAN.md's "every entry says
  why it matters for this specific roster" without repeating the identical rival-team sentence on every
  single row within a position. `InjuryBadge` renders nothing for `ACTIVE`/missing status (a badge only
  earns its place when there's real uncertainty), red for `OUT`/`INJURY_RESERVE` (matches the same set that
  floors `opportunity_score` server-side), amber for `QUESTIONABLE`.
- **`lib/format.ts` gained `formatPercentPoints`**, a deliberately separate function from the existing
  `formatPercent` (which expects a `0-1` fraction and multiplies by 100) -- ESPN's ownership figures
  (`percent_owned`/`percent_started`/`percent_change`) already arrive on a `0-100` scale from the API, so
  reusing `formatPercent` on them would silently 100x every number. Keeping two named functions makes a call
  site's intent unambiguous rather than one function with a scale flag that's easy to pass wrong.
- **Verification**: `pytest -q` 186 passed (171 from Phases 0-9a plus 15 new in
  `sim/tests/test_api_waiver_intelligence.py`); `mypy --strict sim ingest` 21 pre-existing errors (0 new);
  `ruff check sim ingest db scripts` shows only the same pre-existing findings in files this phase didn't
  touch, plus one new `TRY004` finding in the new module consistent with the exact same, already-accepted
  pattern in `ingest/parse.py`/`ingest/scoring.py` (an `isinstance` guard raising `ValueError` for a
  malformed fixture). Web: `tsc --noEmit`, `eslint .`, and `npm run build` (production) all clean. Visually
  verified in a real browser (uvicorn + Next.js dev server, both against the real league,
  `league_id=885686492`) at 375px mobile, 768px tablet, and 1440px desktop, for two different teams: Chinese
  Chongqing Dockers (no real positional need anywhere -- every group renders as "Depth add" or "Streaming
  options") and Captain Jahmyrica (a real TE need -- `Real need` amber callout leads the page, correctly
  naming the one rival, Milking the McCaffinator, also thin at TE); confirmed the QB/RB/K/D-ST groups render
  correctly for both teams with real, per-player reasoning text; confirmed `document.documentElement.scrollWidth
  === clientWidth` at both 375px and 768px (no horizontal scroll) and zero console errors. Hit the same
  `computer` screenshot/scroll tooling artifact Phases 8 and 9a already flagged (a scroll deep into the page
  intermittently hung the tool and once bounced the tab back to the Overview page) -- cross-checked with
  `document.body.textContent` via the JS tool each time, which consistently returned the full, correct real
  content (verified the K/D-ST "streaming options" section and the TE "real need" section this way),
  confirming a tooling artifact rather than an app bug, the same conclusion reached under the same symptom
  in both prior phases.

## Phase 9c — Weekly recap: deferred, not built

PLAN.md scopes Phase 9 as three separate sub-features. Lineup optimizer (9a) and
Waiver intelligence (9b) are both done (see above). Weekly recap (9c) is **not**
built, and this is a deliberate deferral, not an oversight or a skipped task.

**Why:** every one of Weekly recap's required outputs -- biggest winner/loser,
luckiest/unluckiest team (comparing each team's actual score that week against
every other team's actual score), performance of the week, biggest waiver impact
-- is defined entirely in terms of a week that has *actually been played*. The
real league (league_id=885686492) has zero games played anywhere; `matchup.winner`
is `UNDECIDED` for every regular-season matchup. There is no honest way to recap a
week that has not happened.

This is a structurally different situation from every earlier "pre-season" blocker
this project has hit and resolved with synthetic/prospective data:
- Draft Autopsy (Phase 7) needed a real *draft*, which had already happened by the
  time that phase ran -- no synthetic stand-in was needed at all once the league
  drafted.
- Playoff Planner (Phase 8), Lineup optimizer (Phase 9a), and Beat My League
  (Phase 10, if scoped similarly) are all legitimately *prospective* -- "what does
  simulate_seasons() project," not "what already happened." A pre-season answer to
  a forward-looking question is still an honest answer to that question.
- Season replay's alternate-lineup/schedule-neutrality what-ifs (Phase 6) do use
  one sampled "synthetic actual season" -- but that tool's entire framing is an
  explicit hypothetical ("what if a season like this happened"), labeled
  SYNTHETIC everywhere it surfaces, and the user reading it already knows they are
  looking at a constructed scenario, not news.

Weekly recap is different in kind: PLAN.md's own description calls it "the
retention feature -- it should be worth opening on a Tuesday morning." Its entire
value proposition is being genuine, factual news about what actually happened in
the user's actual league. Fabricating a "recap" from a sampled realization --
even clearly labeled SYNTHETIC -- would not produce a usable version of this
feature; it would produce a plausible-sounding lie about a game that was never
played, in a feature specifically designed to be trusted at a glance on a Tuesday
morning. That is a materially worse failure mode than an honestly-labeled
what-if tool, and building it would undermine the entire point of the feature
rather than deliver a reduced-scope version of it.

**Resolution:** deferred, alongside Fantasy Lab and draft replay (see PLAN.md's
"Deferred by choice" section) -- not abandoned, just correctly sequenced after
real games exist to recap. Revisit once `matchup.winner` is no longer
`UNDECIDED` for at least one real week (Phase 3's schema and Phase 5b's
`schedule_view.load_schedule` already track this and will surface it via
`current_week` the moment it's true). No code was written for this sub-feature;
nothing here needs undoing when that day comes.

## Phase 10 — Beat My League

- **Reused Playoff Planner's entire computation wholesale, rather than
  re-simulating anything.** `sim/api/beat_my_league_view.py` reaches into
  `sim.api.playoff_planner_view`'s private `_compute_from_raw` directly (not
  its public, Postgres-taking `compute_playoff_planner`) so the ONE real
  `simulate_seasons()` call plus per-slot Monte Carlo sampling this feature
  needs runs exactly once per request, off the same raw payload this module
  already loaded -- calling the public function instead would force a
  second, redundant `load_raw_payload` round trip. This is a different class
  of reuse than the "small shared rule, reimplemented locally per view
  module" precedent Phase 8/9b established for bench-depth-by-position
  counting (five lines, cheap to duplicate for a different local dataclass
  shape): here the thing being reused is an entire stochastic simulation
  pipeline, and CLAUDE.md's "one simulation engine" rule means that must be
  called once and built on, never re-run. `beat_my_league_view.py`
  constructs zero `np.random.Generator` instances of its own -- title
  probability, playoff probability, and finish distribution for every team
  come straight from `PlayoffSeedOdds` (itself an unmodified read of
  `SimulationResult`); everything else in this module is deterministic
  post-processing/comparison over already-computed numbers.
- **"Structural strength" = a pure selection over Playoff Planner's own
  already-computed `SlotPlayoffStrength` list, not a new formula.**
  Strengths: every slot whose `league_percentile` clears an editorial
  70.0 threshold (`_STRONG_PERCENTILE_THRESHOLD`), falling back to the
  team's single best slot if none clears it -- automatically
  team-differentiating since `league_percentile` is a relative, rank-based
  number (only a minority of teams can clear "top 30%" at any slot).
- **"Structural weakness" = Playoff Planner's own single `weakest_slot`,
  NOT every slot flagged `is_playoff_specific_weakness` -- a real bug caught
  before shipping, the same "verify team-differentiation before using a
  signal" discipline Phase 8/9b already established for this exact
  bench-depth-at-K/D-ST pattern.** An earlier draft of this module selected
  every `is_playoff_specific_weakness` slot per team. Verified live against
  the real league: K clears that flag for all 8 of 8 real teams (nobody in
  this league carries a bench kicker) and D/ST for 6 of 8 -- reporting the
  raw flagged set would have shown "K" as a "structural weakness" for
  nearly every team, true but useless as team-specific advice, exactly the
  "Generic fantasy advice is a failure condition" trap CLAUDE.md warns
  about. Fixed by reusing `TeamPlayoffPlan.weakest_slot` verbatim (Phase 8's
  own `max` by `floor_ratio_delta` among flagged candidates) instead --
  verified this restores real per-team variation matching Phase 8's own
  documented result exactly (6 teams land on D/ST, 1 on TE, 1 on K).
- **"Playoff schedule difficulty" = `TeamPlayoffPlan.recommendation`,
  surfaced verbatim as `playoff_schedule_note`** -- Phase 8's own
  server-synthesized playoff-window narrative, not reworded or recomputed.
  Shown per team in the league-wide comparison table on the page.
- **"Biggest threat" is a lexicographic selection, not a blended composite:**
  filter every OTHER team to those with a real structural strength (per the
  above) at the exact slot label that is also the selected team's own real
  structural weakness (the single `weakest_slot`), then, among that
  candidate set, pick the one with the highest simulated `title_probability`
  -- a real contender who is ALSO specifically built to exploit that
  specific gap, not merely "a good team." Same "no arbitrary weighted score"
  discipline `sim.api.draft_autopsy_view` (best/worst pick) and
  `sim.api.waiver_intelligence_view` (candidate ranking) already established
  for this codebase. If no rival has a real positional overlap with the
  selected team's own weakness (a real, honest possibility -- verified this
  branch fires for some teams live), the fallback is the single
  highest-title-probability rival league-wide, with a reasoning sentence
  that says exactly that ("the strongest team in the league, not a specific
  positional matchup") rather than forcing a positional narrative the data
  doesn't support.
- **"Real advantage" is the mirror-image comparison, anchored to the
  identified threat specifically (not the field in the abstract), so the two
  cards read as one coherent head-to-head:** a slot counts as the selected
  team's real advantage when it is one of their own real strengths AND one
  of the identified threat's own real weakness. Falls back to the selected
  team's single best slot league-wide (with an honest "the threat isn't
  specifically thin here" sentence) when no slot clears both bars.
- **"Which positions not to trade away" reuses Phase 9b's Waiver
  Intelligence Signal 3/4 rule (bench-depth-by-position, per-team, across
  the whole league) applied to the opposite side of the transaction.**
  Waiver Intelligence asks "who should I add" (does a rival have depth I
  lack); this asks "who would benefit if I traded this away" (do I have
  depth a rival lacks). For each of QB/RB/WR/TE (the same
  `_BENCH_DEPTH_RELEVANT_POSITIONS` Phase 9b/7 already established -- K/D-ST
  bench depth is a near-constant in this league, not a real per-team
  signal), a trade caution fires only when BOTH the selected team carries at
  least one real bench player there (named directly, not just counted) AND
  at least one other real team has zero bench depth at that same position.
  `_team_bench_names_by_position` reimplements the counting rule locally
  (not imported from `roster_view`/`waiver_intelligence_view`), following
  those modules' own established precedent: the rule is shared, the local
  data shape (real player names, not just a count) is not. An empty
  `trade_cautions` list is a real, honest result (verified live: Milking
  the McCaffinator has none right now) -- not a bug or a forced claim.
- **Every reasoning sentence names real players, real teams, and real
  numbers, per PLAN.md's explicit requirement.** Player names come from a
  new, purpose-built `_team_slot_starters` (starter names by slot label) and
  the extended `_team_bench_names_by_position` (bench names by position,
  built on the same rule Phase 8/9b already use for counts) -- both parsed
  directly from the same raw payload's roster entries, matching every other
  view module's "re-derive from raw, don't import another module's Postgres-
  only function" convention. Verified live for two different selected teams
  that biggest-threat/real-advantage/trade-caution text is genuinely
  different per team (see the route test
  `test_route_matches_a_direct_compute_call` and the live browser check
  below), not a template with only the team name swapped in.
- **Bug fixed before shipping: ordinal-suffix formatting.** An initial pass
  hardcoded `f"{percentile:.0f}th"` in three reasoning sentences (backend)
  and `.toFixed(0)}th` in the league comparison table (frontend), which
  renders real values like 71 and 86 as "71th"/"86th" instead of
  "71st"/"86th". Fixed with a small `_ordinal()` helper in
  `beat_my_league_view.py` and by reusing `web/lib/format.ts`'s existing
  `ordinal()` (already used elsewhere in this app for 0-indexed finish
  places -- called here as `ordinal(percentile - 1)` since percentile is
  already a 1-indexed number, unlike finish place). Verified live in the
  browser and via a direct curl of the route after the fix.
- **New route: `GET /league/{id}/beat-my-league/{team_id}`**, following
  every established pattern exactly: `resolve_season_id` for the optional
  season, `LeagueNotIngestedError`/`UnknownTeamError` -> 404,
  `IngestError`/`ParamsError` subclasses -> 409, a thin
  `_compute_from_raw`/`compute_beat_my_league` split for fast Postgres-free
  unit tests (`sim/tests/test_api_beat_my_league.py`, 12 new tests,
  including a direct regression test for the weakest-slot bug above and a
  title-probability-matches-playoff-planner-exactly equivalence test).
- **UI: team switcher is a server-navigated `?team=` link row
  (`components/beat-my-league/team-picker.tsx`), matching Lineup
  Optimizer's and Waiver Intelligence's exact established pattern** -- this
  app has no auth/league-picker (Phase 5b), so a real per-team live
  computation needs the same URL-driven "the user's team" selector every
  other per-team page already uses. `ThreatCard` (destructive-tinted) and
  `AdvantageCard` (primary-tinted) sit side by side as the page's headline
  pair -- the one head-to-head comparison that matters -- with
  `TradeCautionList` (brand-accent, matching `RecommendationCallout`'s
  "lead with the action" treatment) directly below. `LeagueComparisonTable`
  (a real shadcn `Table`, per the ui-ux-pro-max skill's stack guidance --
  not a div grid) is the full-league Comparative Analysis Dashboard context
  below the fold, highlighting the selected team ("You" badge) and the
  identified threat ("Threat" badge) in place among all 8 teams sorted by
  title odds; its "Playoff schedule difficulty" column carries the full,
  untruncated per-team narrative and scrolls horizontally within its own
  wrapper at narrow widths, matching the Draft Autopsy board's established
  wide-table convention rather than truncating real text.
- **Verification**: `pytest -q` 198 passed (186 from Phases 0-9b plus 12 new
  in `sim/tests/test_api_beat_my_league.py`); `mypy --strict sim ingest` 21
  pre-existing errors (0 new); `ruff check sim ingest db scripts` shows only
  the same pre-existing findings in files this phase didn't touch (0 new in
  any Phase 10 file). Web: `tsc --noEmit`, `eslint .`, and `npm run build`
  (production) all clean. Visually verified in a real browser (uvicorn +
  Next.js dev server, both against the real league, `league_id=885686492`)
  at 375px, 768px, 1024px, and 1440px for two different selected teams
  (Chinese Chongqing Dockers and Milking the McCaffinator): threat, advantage,
  and trade-caution content is genuinely different per team (different rival
  named as the threat, different slot as the advantage, one team shows a
  real trade caution while the other shows the honest empty state); confirmed
  `document.documentElement.scrollWidth === clientWidth` at all four widths
  (no page-level horizontal scroll) and zero console errors at any width.
  Hit the same known screenshot/scroll tooling artifact Phases 8/9a/9b
  already flagged (a `computer` scroll+screenshot call hung and reported the
  pane as hidden) once, on the first scroll-down attempt; cross-checked
  immediately with a fresh tab plus `get_page_text` and
  `document.body.innerText` via the JS tool, both of which returned the
  full, correct real page content (including the ordinal-suffix fix,
  verified as "71st" not "71th") -- confirmed a tooling artifact, not an
  app bug, the same conclusion reached under the same symptom in three
  prior phases.

## Phase 11 — League History and manager ratings: REMOVED, not just deferred

**Update:** originally deferred (see below), the project owner subsequently made
the call explicit: this feature is removed from the plan entirely, not merely
waiting on data. Asked directly whether the deferred features could be built
without the missing data, the project owner agreed to that approach for Weekly
Recap/Weekly Awards (see those phases' sections) but explicitly declined it for
this one after the distinction was explained -- League History's premise
(multiple completed seasons to compare across) can't be satisfied the same way
Weekly Recap's premise (one played week) can. Weekly Recap only needed one
synthetic week sampled from this league's own real current players and real
fitted projections. League History would need entire fictional past seasons --
invented draft classes, invented rosters, for years that never happened -- which
has no grounding in real data at all. See PLAN.md's "Removed by choice" section
for the parallel entry there. The original deferral reasoning below still
explains why this was flagged as blocked in the first place; nothing about that
changed, only the resolution (removed, not wait-and-revisit).

### Original deferral reasoning (superseded above, kept for context)

Not attempted. Before dispatching this phase, the fixture's
`status.previousSeasons` field was checked and showed `[2025]`, which read as
"one prior season exists, the user just needs to run
`scripts/fetch_fixture.py --season 2025` themselves" -- the same kind of
one-command prerequisite Phase 1 originally had. **The project owner
corrected this directly: this league has no previous season.** Whatever
`previousSeasons` reflects on ESPN's side (the league container's ID
existing since 2025, a league-settings carryover, or something else
ESPN-internal), it does not mean a real season was played by this group of
managers. This is now trusted as ground truth over the JSON field -- the
project owner's direct knowledge of their own league beats an inference from
one status field, and no fetch was attempted on the strength of that field
alone.

**Why this can't be scoped down, unlike Weekly Recap (Phase 9c):** Weekly
Recap is blocked on time (real weeks haven't been played *yet*, in an
otherwise real single season already fully ingested) -- it will unblock
itself the moment games are played. Phase 11 is blocked on this league
having no history at all: "best and worst drafts" (plural), "championships"
(plural), and "all-time manager ratings" all presuppose multiple completed
seasons to compare across. With zero prior seasons, there is nothing to make
"all-time" or "history" mean anything -- a single-season attempt would not
be a reduced version of this feature, it would just be Draft Autopsy (Phase
7) again under a different name, since draft-pick grading is the only one of
Phase 11's five skill dimensions (draft/waiver/trade/lineup/luck) that
doesn't itself require games having been played to compute.

**Resolution:** deferred alongside Weekly Recap, Fantasy Lab, and draft
replay (see PLAN.md's "Deferred by choice" section) -- correctly sequenced
after this league has actually completed at least one full season of its
own real history to look back on. No code was written; nothing needs
undoing when that day comes.

## Phase 12 — Entertainment (power ranking roast only)

*Covers the "power ranking roast" half of PLAN.md's Phase 12 (feature 16) only.
Weekly awards is a separate sub-feature and was NOT built this phase -- see
the dedicated note at the end of this section.*

- **Reused `sim.api.beat_my_league_view` wholesale rather than recomputing
  anything -- required a small, behavior-preserving refactor of that module
  first, verified before writing a line of roast code.** `beat_my_league_view._compute_from_raw`
  ran the entire `simulate_seasons()` + per-slot-sampling pipeline once per
  selected team_id; calling it 8 times (once per team) to build a roast for
  every team would have re-run that whole stochastic pipeline 8x for one
  request, exactly the kind of redundant/approximated re-simulation CLAUDE.md's
  "one simulation engine" rule warns against. Split `_compute_from_raw` into
  `_build_shared_materials` (the one-time simulate_seasons() + profile-building
  pass) and `_compute_team_result` (pure per-team selection over already-built
  materials) -- `compute_beat_my_league` itself is now a two-line composition
  of those pieces with byte-identical behavior, confirmed by re-running all 12
  pre-existing `test_api_beat_my_league.py` tests immediately after the
  refactor (all passed, no test edited) before adding anything new.
- **`sim/api/roast_view.py` calls `_build_shared_materials` exactly once per
  request, then `_compute_team_result` once per team_id** -- one real
  simulation for the whole league's roast, not one per team. This is the
  entire reason the refactor above was worth doing rather than just calling
  `compute_beat_my_league()` in a loop.
- **Exactly which real, already-computed facts each roast pulls from, and
  how:** (1) simulated title-probability rank -- `TeamLeagueProfile.title_probability`,
  the same number Playoff Planner / Beat My League already show, just ranked
  and formatted for comedic framing; (2) a real draft reach --
  `sim.api.draft_autopsy_view`'s own `worst_pick` (the specific named
  alternative player passed over, and the real rank gap), used when that gap
  clears an editorial `_REACH_THRESHOLD` (-3.0 rank-spots, same magnitude as
  Draft Autopsy's own `_GAP_CAUSE_THRESHOLD`); (3) failing that, a real draft
  steal -- that team's own `best_pick`, used when it clears `_STEAL_THRESHOLD`
  (+5.0, deliberately a higher bar than a reach needs, so the backhanded
  compliment only fires for a genuinely notable pick); (4) failing both, Draft
  Autopsy's own already-synthesized `structural_finding` sentence, verbatim
  (never a template -- that sentence is guaranteed real and team-specific by
  Phase 7's own logic); (5) a real zero-bench-depth weakness --
  `beat_my_league_view`'s already-vetted-for-team-differentiation
  `TeamLeagueProfile.weaknesses[0]` (Playoff Planner's single `weakest_slot`,
  not the raw `is_playoff_specific_weakness` flag Phase 8/10 already found is
  NOT team-differentiating in this league -- K clears it for all 8 real
  teams); (6) a real rival threat -- `beat_my_league_view`'s own biggest-threat
  selection (a rival with a real structural strength at exactly this team's
  own weakness, ranked by title probability), only cited when
  `overlapping_slots` is non-empty (the real, honest "no rival specifically
  attacks you" case is silently omitted, never forced). No new scoring
  formula anywhere in this module -- every number is a straight read of
  output another phase already computed and already shipped.
- **Draft material is optional; everything else is not.** `DraftNotAvailableError`
  from `draft_autopsy_view._compute_from_raw` is caught locally and turned
  into `has_draft_data=False` on the response, rather than propagating to a
  409 for the whole roast the way it does for `GET /draft-autopsy` directly.
  Verified live against both leagues: the real league (`885686492`, a
  completed real draft) gets `has_draft_data=True` and a real draft-reach or
  draft-steal or structural-finding sentence in every one of its 8 roasts; the
  SYNTHETIC validation league (`-1990001`, a mock draft with no real pick
  sequence) gets `has_draft_data=False` and every roast still renders with
  real title-rank/bench-depth/rival-threat material -- confirmed in the
  browser, the UI's `DraftDataNote` banner only appears for the SYNTHETIC
  league.
- **`RoastFact` (kind + text) travels alongside every roast as its "receipts"
  -- a machine-readable citation for every sentence, not just prose.** This
  is what makes "every roast must trace to a real fact already computed
  elsewhere" independently checkable rather than an unverifiable claim about
  the prose: `sim/tests/test_api_roast.py` asserts each fact's `kind`
  matches real underlying data (e.g. a `draft_reach` fact's `text` must name
  the actual `worst_pick.player_name`/`alternative_player_name`, and that
  same alternative name must also appear in the rendered `roast` string) --
  13 new tests, all passing, none touching `sim/tests/test_engine.py`.
- **Power-ranking order is `title_probability` descending with `team_id` as a
  deterministic tiebreak** -- `title_rank` (1-indexed) is stamped directly
  onto each `TeamRoast` server-side, so the frontend never re-sorts or
  re-derives rank from raw probabilities (CLAUDE.md's "no analytics logic in
  components," applied to something as small as a sort).
- **No per-team selector on the page, unlike Lineup Optimizer / Waiver
  Intelligence / Beat My League.** This feature is inherently whole-league --
  everyone in the league gets roasted in the same response -- so there is
  nothing for a `?team=` picker to select between; the page renders all 8 (or
  10, for the SYNTHETIC league) cards in a responsive grid.
- **Shareable image export: implemented for real, client-side, via
  `html-to-image` (`toPng`/`toBlob`), not a stub.** Investigated the
  realistic options first: a server-side headless-browser screenshot service
  (Playwright/Puppeteer) would need a new backend process this app has no
  infrastructure for and would violate "UI only, no analytics logic" scope
  creep into `/web` needing a render server; a pure-CSS "looks screenshot-able"
  card with no actual export button would not satisfy PLAN.md's explicit
  "Shareable image export" requirement. `html-to-image` clones the target DOM
  node, serializes it to an SVG `data:` URI, loads that as an `Image`, and
  draws it to a `<canvas>` -- entirely in the browser, no server round trip,
  no new backend. Each `RoastCard` wraps only its visual card (not its export
  buttons) in a `ref`ed div so the buttons themselves are never captured;
  "Download image" builds a PNG data URL and triggers a native browser
  download via a synthetic `<a download>` click, "Copy image" (shown only
  when `navigator.clipboard.write`/`ClipboardItem` are feature-detected
  present) writes a PNG blob straight to the OS clipboard for pasting directly
  into a group chat.
  - **Real bug found and fixed during verification: a hydration mismatch.**
    The `canCopyImage` feature-detection (`"clipboard" in navigator`) was
    originally computed directly during render. Since `navigator` doesn't
    exist during server-side rendering of this "use client" component, the
    server-rendered HTML always omitted the "Copy image" button while the
    client's first client-render included it -- a real hydration error,
    caught via the browser console during verification (`Hydration failed
    because the server rendered HTML didn't match the client`), not
    hypothetical. Fixed with the standard pattern: `canCopyImage` starts as
    `useState(false)` (matching what the server renders) and flips to the
    real feature-detected value inside a `useEffect` after mount. Verified
    clean (zero console errors) on a fresh tab/fresh `.next` build afterward.
  - **Real robustness gap found and fixed during verification:
    `navigator.clipboard.write()` can hang indefinitely rather than resolving
    or rejecting.** Observed directly, twice, in this session's browser
    tooling (both a synthetic DOM `.click()` and a simulated real mouse
    click on "Copy image" left the button disabled with no feedback well
    past any reasonable wait, with zero console error, while the underlying
    `toBlob()` capture itself demonstrably succeeded -- confirmed via a
    `data:image/svg+xml` network response returned `200 OK` in the same
    click). Whether this specific hang is unique to a sandboxed/automated
    browser context or can also happen for a real user (e.g. a
    permission-prompt UI a user never resolves), a promise that can hang
    forever behind a `disabled` button is a real UX bug either way. Fixed
    with a generic `withTimeout()` wrapper (8s) applied to both the
    `toPng`/`toBlob` capture call and the `clipboard.write` call in
    `components/roast/roast-card.tsx`, so the button always recovers to an
    honest "Couldn't export -- try again" state instead of staying stuck.
    This was not a hypothetical hardening pass -- it was written in direct
    response to a reproduced hang.
- **Fact citations ("The receipts") are rendered as a labeled list, never
  truncated with a hover-only tooltip** -- a direct application of the
  ui-ux-pro-max skill's own compact-label guidance ("preserve labels... don't
  clip with a hover-only tooltip; expose full text to keyboard, pointer and
  touch users"), queried explicitly for this page. The citation text is the
  evidence that makes the joke honest, so truncating it would undermine the
  entire point of showing it.
- **Rank iconography: `Trophy` (amber/brand-accent) for the #1 team, `Flame`
  (destructive-red) for the last-place team, nothing for everyone else** --
  both from `lucide-react` (already a dependency), matching MASTER.md's "SVG
  icons, not emoji" rule; queried the ui-ux-pro-max skill's icon domain first
  and found no curated match for a "roast/flame" icon, so this is a plain,
  documented `lucide-react` choice, not a database-backed recommendation.
- **New dependency: `html-to-image` (`^1.11.13`)**, added via `npm install`
  in `/web` -- the only new package this phase needed.
- **Verification**: backend -- `pytest -q` 211 passed (198 from Phases 0-10
  plus 13 new in `sim/tests/test_api_roast.py`); `mypy --strict sim ingest`
  21 pre-existing errors (0 new); `ruff check sim ingest db scripts` shows
  only the same pre-existing findings in files this phase didn't touch (0 new
  in `sim/api/roast_view.py`, `sim/api/beat_my_league_view.py`,
  `sim/api/app.py`, or the new test file). Web: `tsc --noEmit`, `eslint .`,
  and `npm run build` (production) all clean. Visually verified in a real
  browser (uvicorn + Next.js dev server, both against the real league,
  `league_id=885686492`) at 375px mobile and 1440px desktop, across multiple
  teams: rank badges, trophy/flame icons, roast paragraphs, and "the
  receipts" fact lists all render correct real data; confirmed at least two
  teams' roasts are genuinely different (different draft picks, different
  weak slots, different rival threats named) via both screenshots and
  `get_page_text`; confirmed `document.documentElement.scrollWidth ===
  window.innerWidth` at both widths (no horizontal scroll); confirmed the
  SYNTHETIC league (`league_id=-1990001`) renders the `DraftDataNote` banner
  and 10 real, draft-free roasts. Hit the same screenshot/scroll tooling
  artifact flagged in Phases 8/9a/9b/10 (one `computer screenshot` call after
  a page interaction returned a garbled/duplicated render) -- cross-checked
  immediately with a fresh tab plus `get_page_text` and the accessibility
  tree, both of which showed correct, complete real content, and zero console
  errors at any point -- confirmed a tooling artifact, not an app bug, the
  same conclusion reached under the same symptom in four prior phases.

### Weekly awards: deferred, not built

Team of the week, worst start/sit, luckiest/unluckiest, biggest riser/faller --
none of this was built this phase. Every one of these is defined in terms of a
*played week* (comparing actual scores, actual start/sit decisions, actual
week-over-week movement), and the real league (`league_id=885686492`) has zero
games played anywhere (`matchup.winner` is `UNDECIDED` for every regular-season
matchup) -- the identical structural blocker Phase 9c ("Weekly recap: deferred,
not built," above) already worked through for the same reason. PLAN.md's own
words for this whole feature -- "the joke lands because the underlying stat is
true" -- do not hold here yet, since there is no played-week stat to be true
about. Deferred alongside Weekly Recap, correctly sequenced after this league
has actually played at least one real week. No code was written for this
sub-feature; nothing needs undoing when that day comes.

## Phase 13 — AI analyst

- **SDK: `google-genai` (the official, current Google Gen AI Python SDK, `from google import
  genai` / `from google.genai import types`), not the legacy `google-generativeai` package.**
  Confirmed installed (1.75.0), ships a real `py.typed` marker (no mypy override needed, unlike
  apscheduler/scipy), and its `types.FunctionDeclaration`/`types.Tool`/`types.GenerateContentConfig`
  surface is the current documented pattern for manual (non-automatic) function-calling loops --
  used deliberately with `automatic_function_calling=AutomaticFunctionCallingConfig(disable=True)`
  so this module decides exactly when/how each tool runs and logs every real result itself, rather
  than the SDK silently calling plain Python callables and hiding that from the citation-building
  pass.
- **Model: `gemini-flash-lite-latest`, arrived at after a real investigation during this phase's
  own live verification, not picked from memory.** First tried `gemini-2.5-flash` (a dated
  snapshot) -- it still appears in `client.models.list()` but a live call returned a real
  `404 NOT_FOUND: "This model ... is no longer available to new users"` from this freshly-created
  API key. Switched to `gemini-flash-latest` (Google's own stable alias for its current-recommended
  fast tier, not a snapshot) -- this worked functionally (confirmed live, correct real tool calls)
  but was returning real, reproducible `503 UNAVAILABLE: "This model is currently experiencing high
  demand"` errors during this verification window, confirmed via direct raw REST calls with no SDK
  retry logic involved (so not an artifact of this codebase's retry/timeout handling). Switched to
  `gemini-flash-lite-latest` -- responded in under a second with correct real tool calls in every
  live test that followed, and is also the more cost-conscious choice PLAN.md's own "be reasonably
  economical" instruction asks for. All three findings are recorded directly in
  `sim/api/analyst_view.py`'s `MODEL_NAME` comment, not just here, since a future session hitting
  the same 404/503 pattern needs the same investigation trail.
- **Tool-to-endpoint mapping, one thin wrapper per tool in `sim/api/analyst_tools.py`, each calling
  the same already-tested `sim.api` function its matching HTTP route calls** (never a second HTTP
  round trip, never a re-derivation): `get_team_odds` -> `sim.api.cache.read_cached_simulation`
  (`GET /simulation`); `get_waiver_targets` -> `sim.api.waiver_intelligence_view.compute_waiver_intelligence`;
  `get_playoff_outlook` -> `sim.api.playoff_planner_view.compute_playoff_planner`; `get_league_threats`
  -> `sim.api.beat_my_league_view.compute_beat_my_league`; `get_trade_impact` ->
  `sim.api.params_loader.load_league` + a live `sim.engine.simulate_seasons()` call via
  `roster_overrides`, the identical engine call `POST /whatif` and the Trade Builder UI (Phase 6)
  already make, just invoked in-process instead of over a second HTTP hop within the same request.
- **`get_roster_weaknesses` -> `GET /league/{id}/roster` (`sim.api.roster_view`: `risk_rating`,
  `positional_concentration`, per-player floor/ceiling/availability) -- chosen over
  `beat_my_league_view`'s playoff-window weakness selection, and documented as a deliberate choice,
  not a default.** Roster view's signal is the more literal, direct answer to "what is my roster's
  weakness": it's roster composition itself (which starting positions have zero same-position bench
  depth, which players carry real availability risk) rather than a derived, playoff-simulation-
  specific signal. Beat My League's own weakness selection (`TeamPlayoffPlan.weakest_slot`) is not
  wasted -- it's exactly what backs `get_league_threats` instead (the threat/advantage/trade-caution
  narrative Phase 10 already built is a more natural, richer answer to "who is my biggest threat"
  than to "what is my weakness" in isolation). This keeps the two tools genuinely complementary
  rather than two thin wrappers around the same underlying signal.
- **Team-name resolution (`_resolve_team_id` in `sim/api/analyst_tools.py`) is deliberately
  conservative: exact case-insensitive match, then a substring match ONLY if unique, else an honest
  error listing every real team name.** The model is never allowed to guess which `team_id` a name
  maps to -- CLAUDE.md's "no invented numbers" rule applied to team identity, not just stats. The
  bound "my team" (from the URL's `team_id`, chosen by the frontend's team-switcher, matching every
  per-team page since Lineup Optimizer) is always the default when a tool's optional `team_name`
  arg is omitted, never guessed by the model either.
- **`get_trade_impact` hard-requires real, currently-rostered starter names on both sides and
  refuses a vague "should I trade with X" with no players named** -- the system prompt tells the
  model to first call `get_roster_weaknesses` / `get_league_threats` / `get_waiver_targets` to find
  a real, specifically-named candidate trade if the user didn't name one, then call this tool with
  those exact real names. Verified live: asked "Should I trade with Milking the McCaffinator? I
  could offer them my Eagles D/ST for one of their bench players" (deliberately vague on the receive
  side) -- the model called `get_roster_weaknesses` on both teams first, found a real bench player
  (Rome Odunze), then called `get_trade_impact` with that exact real name on both sides. Player-name
  resolution and the swapped-roster construction (remove the traded-away player's `PlayerParams`
  from that team's real current starters, add the traded-in one) mirrors
  `web/components/whatif/trade-builder.tsx`'s exact logic, and the before/after `simulate_seasons()`
  calls share one seed (common random numbers) so the reported delta reflects only the roster
  change, the same pattern `whatif-compare/route.ts` already established for the Trade Builder UI.
- **Real bug found and fixed during this phase's own live verification, before the UI was ever
  touched: `get_playoff_outlook`'s `projected_seed` crashed the real Gemini call outright.**
  `scipy.optimize.linear_sum_assignment` (inside `sim.api.playoff_planner_view`) returns
  `numpy.int64` seed indices; `sim/api/app.py`'s Pydantic response model silently coerces that to a
  plain `int` on the HTTP path, but this phase's raw-dict tool result skips Pydantic entirely, so
  `google-genai`'s own `json.dumps()` of the tool result raised `TypeError: Object of type int64 is
  not JSON serializable` -- caught live (not simulated) via a real `curl` against the running
  service, root-caused with a standalone script that walked every tool's result through `json.dumps`
  field-by-field. Fixed with an explicit `int(...)` cast in `tool_get_playoff_outlook`, and a new
  regression test (`test_every_tool_result_is_json_serializable`) added to
  `sim/tests/test_api_analyst_tools.py` that runs every tool's real result through `json.dumps`
  directly, specifically so a similar numpy leak in any of the six tools fails a fast, offline,
  free test next time instead of only a live (paid) Gemini call.
- **Citation/span design: the model never emits a citation marker itself -- a deterministic,
  server-side, fully offline-testable pass matches percentages the model actually wrote in its own
  final text against real numbers extracted from that turn's tool results.**
  `sim/api/analyst_view._extract_citable_numbers` is plain field extraction (never a computation)
  over each tool's already-real result dict, normalizing every citable value onto one consistent
  0-100 percent scale (a probability fraction like `0.223` is multiplied by 100; an ESPN
  `percent_owned` figure, already 0-100, is used as-is -- covered by its own regression test so the
  two scales are never conflated). `_match_citations_in_text` then regex-scans the model's own
  `reply` for percentage-shaped substrings (e.g. "22.3%") and links each one to the closest real
  citation within a small rounding tolerance (0.5 percentage points, covering the model rounding a
  real value to the nearest whole percent or one decimal) -- an unrelated percentage with no
  close-enough real citation is left as plain, unlinked text rather than forced to match. This was
  chosen deliberately over asking the model to emit an explicit citation-index token next to every
  number: an LLM reliably emitting an exact index token is not something this codebase can verify or
  unit-test without a live (paid) model call every single time, whereas matching a number the model
  already wrote back against real tool data is fully deterministic and covered by
  `sim/tests/test_api_analyst_view.py` with zero network calls. The response
  (`AnalystChatResponse.citations`/`.spans`) gives the frontend exact character offsets into `reply`
  plus the real value/subject/source-tool for each -- `web/lib/analyst.ts::tokenizeMessage` (see
  below) is the only place that structure gets turned into a rendered `<StatChip>`, never a second
  computation.
- **No server-side chat session state -- the client resends the full transcript on every
  request.** `AnalystChatRequest.messages` is the complete oldest-first turn history; `sim.api`
  has no session store anywhere else in this app (no auth, no league-picker, per Phase 5b), so this
  matches that existing design rather than introducing the first piece of server-side session state
  in the whole codebase. Internal tool-calling round trips within one model turn are never persisted
  -- only user/model text turns are, matching `sim.api.analyst_view.AnalystMessage`'s documented
  contract.
- **Frontend team scoping: `?team=` server-navigated links (`components/analyst/team-picker.tsx`),
  identical to Lineup Optimizer / Waiver Intelligence / Beat My League's established pattern** --
  this app has no auth/league-picker, so "my team" for a brand-new conversation is chosen the same
  URL-driven way every other per-team page already does. Switching teams intentionally resets the
  chat (`key={selected.team_id}` on `<AnalystChat>`) -- a different team is a different conversation
  scope, not a client-side toggle on the same one.
- **Next.js route (`app/api/league/[leagueId]/analyst/[teamId]/route.ts`) is orchestration only --
  a thin pass-through to `POST /league/{id}/analyst/{team_id}`, matching
  `whatif-compare/route.ts`/`season-replay/route.ts`'s exact established "no analytics logic, just
  forwarding" pattern.** `GEMINI_API_KEY` is read only by the Python `sim` service
  (`sim/api/env.py`/`sim/api/analyst_view.py`); it never reaches this Next.js route, the browser
  bundle, or any client component -- the same "web layer's only path to the sim API" boundary
  `lib/api.ts` already enforces for every other feature.
- **New non-destructive `.env` loader (`sim/api/env.py`), called once at `sim/api/app.py` import
  time.** No earlier phase's `sim.api` service ever needed to read `.env` itself (`DATABASE_URL` has
  a safe non-secret default; `ESPN_S2`/`SWID` are only ever read by the one-off
  `scripts/fetch_fixture.py` CLI script). `GEMINI_API_KEY` has no safe default and is the first
  secret the always-running FastAPI service itself needs, so `uvicorn sim.api.app:app` must pick it
  up from the repo-root `.env` without the caller exporting it manually first. Mirrors
  `scripts/fetch_fixture.py::load_env`'s exact parsing and its exact safety property
  (`os.environ.setdefault`, never overwrite a real shell-exported value) -- and, per this phase's
  explicit secrets discipline, never logs or prints anything it loads.
- **`pyproject.toml` gained a real `[project]` table (this repo's first) with `google-genai` as an
  explicit dependency, per this phase's own instruction -- deliberately NOT a retroactive backfill
  of every other ad hoc `pip3 install` from Phases 0-12** (fastapi, psycopg, scipy, apscheduler,
  etc. stay exactly as ad hoc-installed as before; see docs/decisions.md Phases 1/3/4 for why that
  pattern exists). Narrowly scoped to what this phase's brief actually asked for.
- **Adding `[project.requires-python]` had a real, unwanted side effect, caught before it shipped:
  it changed ruff's auto-detected lint target-version, which activated ~28 new "modernize for a
  newer Python" findings (`UP017`, `datetime.UTC`) across pre-existing test files this phase never
  touched.** Fixed by explicitly pinning `target-version = "py310"` under `[tool.ruff]`, matching
  ruff's own prior unset-target-version fallback -- confirmed with a `git stash`/re-run comparison
  that this restores the exact pre-existing 5-finding baseline (0 new). A lint-configuration choice
  only; `requires-python = ">=3.12"` itself is accurate and unchanged (CLAUDE.md already states
  Python 3.12 for `/sim`).
- **Citation-rendering bugs found and fixed live in the browser, not just in code review --
  documented because both are genuine, non-obvious interactions, not typos.** (1) A first two-pass
  design (split text by citation span, then bold-format each leftover text fragment independently)
  left stray literal `**` characters visible whenever a citation happened to sit inside a bold run
  (the model's own habit: `**18.2%**`, bolding the whole clause including the cited number) --
  because the two `**` delimiters ended up in two different fragments, split apart by the citation
  in between, so neither fragment had a complete pair to recognize. Fixed by replacing the two-pass
  design with `web/lib/analyst.ts::tokenizeMessage`, a single left-to-right pass that tracks
  bold/italic state and citation-span position simultaneously, so a citation nested inside a
  bold/italic run renders as a `<StatChip>` (dropping the surrounding markers entirely, not
  preserving them) with no possible fragment-boundary mismatch. (2) While fixing that, also handled
  two more plain LLM markdown habits verified live in real responses: `#`/`##`/... headings (dropped,
  rest of the line renders bold) and `* `/`- ` line-start bullets (replaced with a real "•" glyph) --
  plus lone `*italic*` runs (verified live: "rival teams *Captain Jahmyrica* and *Milking the
  McCaffinator*" rendered with literal asterisks before the fix, real italics after). All of this
  lives in one pure, offline-testable function (`web/lib/analyst.ts`), composed by
  `components/analyst/message-content.tsx` into JSX -- CLAUDE.md's "no analytics logic in
  components" boundary, applied to formatting/rendering logic instead of a computed statistic.
- **`StatChip` (`components/analyst/stat-chip.tsx`) uses the existing `--brand-accent` design
  tokens (amber, established in Phase 5b), not a new color** -- consistent with this app's existing
  "amber = highlight/CTA/real-recommendation" convention (`RecommendationCallout`,
  `TeamPlayoffPlan`'s champion segment, etc.), and satisfies PLAN.md's literal words for this
  feature ("When the analyst mentions a probability, show it") as a real inline component, not a
  bare number in a paragraph. The chip's own text (`citation.display`) is always plainly visible,
  never hover-only, per MASTER.md's compact-label accessibility guidance queried for this phase; the
  `title` attribute adds supplementary (not essential) context about which tool produced the number.
- **Verification -- backend**: `pytest -q` 242 passed (211 from Phases 0-12 plus 31 new: 17 in
  `sim/tests/test_api_analyst_tools.py`, 14 in `sim/tests/test_api_analyst_view.py`); `mypy --strict
  sim ingest` 21 pre-existing errors (0 new, including in the two new test files and
  `sim/api/analyst_view.py`'s one narrow `cast(Any, ...)` at the one call boundary where
  `google-genai`'s own `contents` parameter type is stricter than necessary under mypy's
  invariant-list rule -- documented inline, not a blanket per-file ignore); `ruff check sim ingest`
  5 pre-existing findings (0 new). `sim/tests/test_engine.py` and its golden values were not
  touched. All new Postgres-backed tests use the real league fixture (`league_id=885686492`)
  through the real `ingest_league` path, per this file's own established convention; `run_analyst_turn`
  tests inject a fake `genai.Client` stand-in (built from real `google.genai.types` response
  objects, not mocked types) so the tool-execution/citation-matching logic is verified with zero
  real network calls, matching CLAUDE.md's "fixtures, not live calls" discipline extended to this
  new external dependency.
- **Verification -- frontend**: `npx tsc --noEmit`, `npx eslint .`, and `npm run build` (production)
  all clean throughout, including after every live-verification-driven fix.
- **Verification -- real, live, paid Gemini round trips (not mocked/stubbed), against the real
  league (`league_id=885686492`) via a real running `uvicorn` + Postgres stack, covering every
  question in PLAN.md's "Handles" list, each cross-checked against a direct `curl` of the real
  underlying endpoint for exact-match confirmation:**
  - "Why am I projected to lose the title this year?" -> `get_team_odds` + `get_playoff_outlook`;
    reply cited 16.7%/64.4% (team 1's own title/playoff odds), 22.3% (the real title-odds leader),
    and the real D/ST/K playoff-window weakness with exact real floor-ratio numbers (67%/87%) --
    all confirmed byte-for-byte against `GET /simulation` and `GET /playoff-planner`.
  - "What is my biggest weakness?" -> `get_roster_weaknesses`; reply named the exact real
    `positional_concentration` strings ("No bench depth at D/ST", "No bench depth at K") --
    confirmed against `GET /roster`.
  - "Who is my biggest threat in this league and why?" -> `get_league_threats`; reply named the
    real rival (team_id=4, "."), the real overlapping slot (D/ST), the real advantage slot (FLEX,
    Jeremiyah Love), and a real trade caution (Harold Fannin Jr., naming both real rivals with zero
    TE bench depth) -- confirmed field-for-field against `GET /beat-my-league/1`.
  - "What should I target on waivers this week?" -> `get_waiver_targets`; reply named real
    candidates with real projections/ownership at every position -- confirmed against
    `GET /waiver-intelligence/1`.
  - "Should I trade with Milking the McCaffinator? ... one of their bench players." ->
    `get_roster_weaknesses` (both teams) then `get_trade_impact`; reply cited real before/after
    title/playoff probabilities and real deltas for both teams from a genuine live
    `simulate_seasons(n_sims=2000)` call (seed `4137697144`) -- deltas verified to equal
    `after - before` exactly on the raw tool result.
  Verified in the actual browser (not just via `curl`) too: submitted real questions through the
  live chat UI at desktop (1280px+), tablet (768px), and mobile (375px) widths; confirmed the
  loading state (`aria-busy`, spinner, disabled input) renders correctly while a live call is in
  flight; confirmed zero console errors on fresh tabs; confirmed `document.documentElement.scrollWidth
  === clientWidth` at 375px (no page-level horizontal scroll) even with a long multi-position waiver
  response. Hit the exact screenshot/interaction-hang tooling artifact flagged in every phase since
  Playoff Planner (Phase 8) once during this verification (a `left_click` call timed out with "the
  Browser pane is currently hidden") -- opened a fresh tab per that established playbook, which
  resolved it immediately; also hit one genuinely stale-console-buffer false alarm (an old renamed-
  function error kept appearing in `read_console_messages` after the fix already shipped and
  `npm run build` was already clean) -- confirmed it was a stale buffer, not a live bug, the same
  way prior phases confirmed their own tooling artifacts, by opening a fresh tab and re-checking.
  A real, external Gemini-side 503 "high demand" period on `gemini-flash-latest` was also observed
  live during this same window (see the model-choice note above) -- not a tooling artifact, a real
  transient upstream condition, worked around by the model switch rather than by retrying blindly.

## Phase 14 — Dark Glass Makeover (foundation + dashboard)

- **Deliberate, approved reversal of Phase 5a's light-theme palette, not an
  oversight.** The project owner requested a dark, glassmorphic/neumorphic,
  glowing, motion-heavy theme referencing a soccer-club-management dashboard
  and a live-sports betting dashboard. That directly conflicts with
  `design-system/MASTER.md`'s original, reviewed-and-approved dials (light
  only, Motion 3/10 "Subtle", "Ornate design" explicitly listed as an
  anti-pattern). Rather than silently overwrite an approved decision, this
  was brainstormed, written up as
  `docs/superpowers/specs/2026-08-13-dark-glass-makeover-design.md`, and
  explicitly approved before implementation — the project owner chose
  Option A ("supersede MASTER.md") over keeping a light/dark toggle or
  skipping the docs update.
- **Brand accent corrected from yellow to neon green (`#39FF14`) mid-review**,
  before implementation started — the spec's first draft used `#D7FF3F`
  (yellow), the project owner asked for green specifically. `TEAM_CHART_COLORS`
  and every brand-accent CSS comment were updated to match; no yellow value
  shipped anywhere.
- **Pushed the change down into design tokens and shared `components/ui/*`
  primitives first**, rather than touching all 11 pages at once. Every page
  that already composes `Card`/`Button`/`Badge`/`Table`/`Tabs`/`Tooltip`
  inherits the new dark-glass look automatically from `globals.css`'s new
  `:root` values — confirmed by reading `badge.tsx`, `tabs.tsx`,
  `tooltip.tsx`, `separator.tsx`, and `skeleton.tsx` before deciding none of
  them needed code changes (they reference semantic tokens like `bg-muted`,
  `text-foreground`, `bg-primary` already). Only `Card` (glass blur), `Button`
  (glow shadow + new `accent` variant), and `Table` (glow row hover) needed
  actual className edits.
- **Scope explicitly limited to the shared foundation plus one full
  reference page** (the league dashboard) rather than all 11 pages, per the
  spec's own non-goals. The other 10 pages (`power-rankings`, `risk`,
  `whatif`, `draft`, `playoffs`, `lineup-optimizer`, `waivers`,
  `beat-my-league`, `roast`, `analyst`) get the token/primitive changes for
  free but no page-specific motion work yet — that's fast-follow work once
  this foundation is confirmed working, not invented ahead of time.
- **`chart.tsx`'s SVG glow-filter technique (called out in the spec) was
  deferred rather than built speculatively.** No page in this phase's scope
  renders a chart (the dashboard has none), so implementing it now would
  mean shipping code with zero consumers and no way to visually verify it
  actually works — deferred to the phase that retthemes `power-rankings`
  (the first chart-consuming page), where it can be built and verified
  against a real chart. `lib/chart-colors.ts`'s palette was still updated now
  since it's a trivial, low-risk constant change independent of the filter
  work.
- **`StandingsTable` and `CurrentMatchupCard` became Client Components**
  (`"use client"`) to use Framer Motion (`motion.tbody`/`motion.tr`,
  `useReducedMotion`, `TiltCard`). Both are still rendered from
  `app/league/[leagueId]/page.tsx`, a Server Component, with plain
  serializable props (`simulation`, `schedule`) — a standard Next.js
  Server-Component-renders-Client-Component-children pattern, not a
  page-level `"use client"` conversion. `StandingsTable`'s sort/tally logic
  (`mean_wins` ordering, `tallyActualRecords`) was already pure
  presentational sorting of API-supplied values before this phase; moving
  where it executes (client instead of server) doesn't add new analytics
  logic, and the values themselves are untouched.
- **`AnimatedMeter` is additive, never a replacement for the formatted
  percentage text** — `StandingsTable`'s Playoff %/Title % cells still
  render `formatPercent(...)` exactly as before, with the meter rendered
  alongside it, animating the same already-computed API value (never
  re-derived, never rounded differently).
- **Every new text/background color pairing was checked against WCAG 4.5:1
  before being chosen** (documented in the spec and in
  `web/app/globals.css`'s new `:root` comment): foreground-on-background
  ~16:1, muted-foreground-on-background ~6.3:1, primary-foreground-on-primary
  ~5.2:1 (which is why primary is `#2563EB`, not the brighter `#3B82F6` used
  for glow/ring purposes only), destructive-on-background ~7:1,
  brand-accent-foreground-on-brand-accent ~15.5:1 (dark text only —
  white-on-`#39FF14` measures ~1.4:1 and was rejected).
- **Verification**: `cd web && npx tsc --noEmit`, `npx eslint .`, and
  `npm run build` (production) all clean after every task. `/web` has no
  vitest/unit-test harness configured today (despite `CLAUDE.md`'s general
  mention of one — not actually wired up for `/web` in this repo), matching
  every prior phase's own verification approach for this directory.
  Visually verified in a real browser (Next dev server) at desktop and 375px
  mobile widths: dark palette renders correctly, `Card`/`Button`/`Table`
  glow/glass treatments show and don't shift layout, the sidebar icon rail
  is keyboard-navigable with visible focus and working tooltips, the
  dashboard's four cards stagger in on load and its standings meters animate
  from 0 to their real values, `prefers-reduced-motion` disables all of the
  above (instant, static rendering), and no page-level horizontal scroll at
  375px.
