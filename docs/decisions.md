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
