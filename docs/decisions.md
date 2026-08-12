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
