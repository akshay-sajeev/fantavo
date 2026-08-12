# Build Plan

Work through this file one phase at a time.

## Rules for this file

**One phase per session.** Do not start the next phase in the same session, even
if the current one finished quickly. Fresh context per phase is the point.

**Phases 5 onward are full-stack.** Each ships endpoint plus UI plus commit. A
phase is not done when the API returns correct JSON — it is done when you can see
the feature in the browser.

**Every phase ends with a commit.** If tests are red, the phase is not done.

**Append decisions to `docs/decisions.md`** at the end of each phase — one line per
non-obvious choice and why. Sessions start with an empty context window; this file
is how the reasoning survives.

**Never edit golden test values to make a test pass.** If `test_golden_*` fails,
the model changed. Understand why first. `scripts/regenerate_golden.py` exists for
deliberate changes only.

**Never invent a numeric constant.** If a projection, variance or availability
value is missing, raise and say so. A plausible-looking constant produces a
beautiful app displaying meaningless probabilities.

**One simulation engine.** Every probability comes from `simulate_seasons()`. If a
phase seems to need a second simulation path, stop and explain why.

## Design system

UI work uses the **ui-ux-pro-max** skill. Install once, before Phase 5:

```
/plugin marketplace add nextlevelbuilder/ui-ux-pro-max-skill
/plugin install ui-ux-pro-max@ui-ux-pro-max-skill
```

Or via CLI, from the repo root:

```
npm install -g ui-ux-pro-max-cli
uipro init --ai claude
```

Requires Python 3.x for its search script. The skill auto-activates on UI requests
in Claude Code — no slash command needed.

**Generate the design system once, in Phase 5, and persist it.** Every later phase
reads `design-system/MASTER.md` rather than regenerating. Without this, each phase
invents its own visual language and the app ends up looking like four products
stitched together.

Relevant to this project: the skill's BI/Analytics styles include *Predictive
Analytics*, *Data-Dense Dashboard* and *Comparative Analysis Dashboard*. This app
is a forecasting tool, so those are the right neighbourhood — not a marketing
landing page style.

## Progress

Foundation
- [x] Phase 0 — Repo setup
- [ ] Phase 1 — Ingest
- [ ] Phase 2 — Projection parameters
- [ ] Phase 3 — Persistence
- [ ] Phase 4 — API

Product (full-stack)
- [ ] Phase 5 — Design system + dashboard + power rankings + risk *(1, 2, 11)*
- [ ] Phase 6 — What-if and trades *(4, 8)*
- [ ] Phase 7 — Draft Autopsy *(5)*
- [ ] Phase 8 — Playoff Planner *(12)*
- [ ] Phase 9 — Weekly loop *(9, 10, 15)*
- [ ] Phase 10 — Beat My League *(7)*
- [ ] Phase 11 — League History and manager ratings *(13)*
- [ ] Phase 12 — Entertainment *(16)*
- [ ] Phase 13 — AI analyst *(6, 17)*

Feature 3 (season simulator) is already built — it is `sim/engine.py`.
Deferred by choice: draft replay and Fantasy Lab (14). See the end.

---

## Phase 0 — Repo setup

```
Read CLAUDE.md — it describes the intended repo layout. The files here are
currently flat. Reorganize to match it:

- sim/engine.py, sim/tests/test_engine.py, with __init__.py in both
- scripts/fetch_fixture.py, scripts/regenerate_golden.py
- CLAUDE.md, pyproject.toml, .gitignore, .env.example at root
- Create empty docs/, fixtures/, ingest/tests/, db/, web/ with .gitkeep

Use `git mv` for tracked files, plain mv otherwise.

Then install numpy, pytest, requests and run `pytest -q`. All 25 tests must
pass. If imports break after the move, fix the paths — do not modify the tests
or the golden values.

Confirm .env is NOT tracked. Stage everything, show me `git status` and the
full file list. Stop before committing.
```

**Done when:** 25 tests pass, `.env` absent from `git status`.

Then, after reviewing the staged list: commit and push to a **private** repo.

---

## Phase 1 — Ingest

**Prerequisite:** run `python scripts/fetch_fixture.py` yourself first. It needs
your cookies and is the only thing that touches ESPN.

```
Read CLAUDE.md, sim/engine.py, and fixtures/league_raw_2026.json.

Build /ingest: parse that fixture into LeagueParams, TeamParams and
PlayerParams. Typed dataclasses, mypy strict. Tests run against the fixture
only — no network calls anywhere in this package.

Requirements:
- Compute scoring from the league's own scoringSettings. Do not assume
  standard or PPR.
- Read flex eligibility from eligibleSlots, not from listed position.
- Keep scoringPeriodId and matchupPeriodId distinct. Different fields, not
  always equal.
- Build the schedule array in the shape LeagueParams validates.

STOP before writing the projection mapping. ESPN gives point projections, but
the engine needs mean, sd AND availability. Propose 2-3 options for deriving
sd and availability with their tradeoffs, and let me choose. Do not pick one
yourself.

Done when: pytest passes and `python -m ingest.demo` prints a league summary.
```

**Commit:** `Ingest: parse ESPN fixture into engine params`

---

## Phase 2 — Projection parameters

Highest-risk phase in the project. Every probability the app shows inherits its
credibility from these numbers.

```
Read docs/decisions.md for the approach chosen in Phase 1.

Implement it in sim/params/. Requirements:
- Every parameter has a documented source: fitted from data, or an explicit
  stated assumption with rationale in a docstring.
- Raise on missing data rather than substituting a default.
- Include a validation script that prints simulated team-week score
  distributions so I can sanity check against real fantasy scoring.

Sanity check before calling this done: a 10-team league should give the best
roster roughly 15-25% title odds. Above 35% means variance is too low — a
modelling bug, not a strong team.

Done when: the pipeline runs fixture -> params -> simulate_seasons and prints
title odds for every team.
```

**Commit:** `Params: derive scoring distributions from ESPN projections`

---

## Phase 3 — Persistence

```
Read CLAUDE.md and /ingest.

Build /db: migrations for normalized tables (league, team, player, roster,
matchup, scoring_settings) plus JSONB columns holding the raw ESPN payload, so
history can be reprocessed without re-fetching.

Add idempotent upsert to /ingest.

Done when: ingesting the same fixture twice produces byte-identical DB state.
Write that as a test.
```

**Commit:** `DB: schema and idempotent ingest`

---

## Phase 4 — API

Should be thin. The hard work already exists.

```
Read sim/engine.py.

Build a FastAPI service in /sim exposing:
- GET  /league/{id}/simulation   (cached precomputed results)
- POST /league/{id}/whatif       (live, n_sims=2000, roster overrides)

Both call simulate_seasons(). No analytics logic in route handlers.

Add a scheduled job that precomputes and caches the full simulation per league.

Done when: curl against a locally ingested league returns title odds identical
to a direct engine call with the same seed.
```

**Commit:** `API: simulation and what-if endpoints`

---

## Phase 5 — Design system, dashboard, power rankings, risk

*Covers features 1, 2, 11. First phase with UI. Use plan mode (Shift+Tab).*

Two sessions. Do not merge them — the design system needs review before anything
is built on it.

### 5a — Generate and persist the design system

```
Use the ui-ux-pro-max skill to generate a design system for this project and
persist it, so later phases read it instead of regenerating.

Context for the generator: this is a fantasy football analytics tool. Its core
output is probability — championship odds, playoff odds, finish distributions,
confidence ranges. It is a data-dense predictive dashboard, not a marketing
site. Users check it weekly on both phone and desktop. Stack is Next.js with
Tailwind and shadcn/ui.

Run the generator with --design-system --persist so it writes
design-system/MASTER.md.

Then also query the skill for chart recommendations specifically for
probability distributions and ranked comparisons, and record what it suggests
in MASTER.md.

Show me MASTER.md before building anything. I want to review the palette,
typography and chart choices first.
```

**Commit:** `Design: persist ui-ux-pro-max design system`

### 5b — Build the dashboard

```
Read design-system/MASTER.md and follow it. Use the ui-ux-pro-max skill for
component work and run its pre-delivery checklist before you finish.

Build in /web (Next.js, Tailwind, shadcn/ui):

1. League dashboard — standings, rosters, current matchup, remaining schedule.
   Lead with analysis, not a copy of ESPN's UI. Where roster strength and
   record disagree, say so on the page.

2. Power rankings ordered by simulated title probability from the API. NOT a
   computed score — no weighting logic anywhere in the frontend. Show playoff
   odds and finish distribution alongside each team.

3. Roster risk panel. The engine already samples per-player availability, so
   this is a view over existing output, not new modelling. Per player show
   expected availability, floor and ceiling; roll up to a team risk rating and
   flag positional concentration.

Probabilities must render as distributions where the shape matters, not bare
percentages. A 21% title chance with a wide finish distribution means something
different from 21% with a narrow one.

No analytics logic in components — the web layer renders what the API returns.
Mobile layout is not optional; this gets checked on a phone during games.

Show me the component structure and data flow before implementing.
```

**Commit:** `Web: dashboard, power rankings, risk panel`

---

## Phase 6 — What-if and trades

*Covers features 4 and 8 in full. Backend should be small — it is the same engine
with different inputs. If it grows, something was built wrong earlier.*

```
Read sim/engine.py (especially roster_overrides and the regular/opp arrays),
and design-system/MASTER.md. Check for design-system/pages/whatif.md and
prioritize it if it exists.

BACKEND — four what-if types, all reusing simulate_seasons:
1. Trade — swap players between two teams via roster_overrides.
2. Alternate lineup — replay the season over actual historical scores with the
   optimal lineup each week. Actual record vs optimal record.
3. Schedule neutrality — expected record if every team played every other team
   every week. A permutation over existing weekly score arrays, not a new
   simulation.
4. Roster swap — arbitrary starter override.

Do not write a new simulation path for any of these. If you think you need
one, stop and explain why.

UI — use the ui-ux-pro-max skill:
- Trade builder: pick players from two rosters, see before/after title and
  playoff probability for BOTH teams simultaneously.
- Show asymmetry as the headline. A trade that helps you 4 points and your
  opponent 9 should say so plainly and visibly — that framing is the entire
  value of the feature, so it must not be buried in a table.
- Scenario results appear inline without a page navigation. These run live at
  n_sims=2000, so show a loading state.

Run the skill's pre-delivery checklist before finishing.
```

**Commit:** `What-if: trades, optimal lineup, schedule neutrality, full-stack`

---

## Phase 7 — Draft Autopsy

*Covers feature 5.*

```
Read the mDraftDetail block in the fixture, /ingest, and
design-system/MASTER.md.

BACKEND:
- Per-pick value vs what was actually available at that slot
- Best and worst decisions, with the specific alternative on the board
- Positional strategy grades (RB/WR/TE/QB/bench)
- The structural finding, not just pick grades — e.g. "you waited too long on
  RB depth, which forced low-upside options later"

Grade picks against players available at that pick, not against final season
outcomes. Hindsight grading is easy and useless.

UI — use the ui-ux-pro-max skill:
- Draft board view with per-pick grading
- Best and worst decision called out prominently
- Positional grades as a scannable summary
- The structural narrative given more weight than the individual grades

Out of scope: replaying how the rest of the draft would have unfolded. See the
deferred section.
```

**Commit:** `Draft: autopsy, full-stack`

---

## Phase 8 — Playoff Planner

*Covers feature 12. Most useful mid-season — do this before Phase 9 if the season
is underway.*

```
Read sim/engine.py and design-system/MASTER.md.

BACKEND: run the simulator restricted to weeks 15-17 rather than adding a
separate playoff-only model. Produce projected playoff schedule, seeding odds,
per-player strength of schedule for those weeks, and positional weaknesses that
only appear during the fantasy playoffs.

UI — use the ui-ux-pro-max skill:
- Playoff bracket projection with seeding probabilities
- Weeks 15-17 schedule strength per roster slot
- One clear actionable recommendation: which position to target now, before the
  rest of the league notices

Lead with the action, not the data. The point of this feature is doing
something in October, not admiring a chart.
```

**Commit:** `Playoffs: weeks 15-17 planner, full-stack`

---

## Phase 9 — Weekly loop

*Covers features 9, 10, 15. Three separate sessions, each full-stack, each its
own commit. All read design-system/MASTER.md and use the ui-ux-pro-max skill.*

**Lineup optimizer (feature 10)**
```
BACKEND: three lineups — current, safest (highest projected floor), highest
upside (best title equity, not best mean). "Highest upside" means maximising
championship probability, which is not the lineup maximising expected points.
Use the simulator.

UI: all three side by side with projected ranges, not point estimates. Make the
tradeoff legible — the safe lineup and the upside lineup are different bets,
and the UI should show why someone would pick either.
```

**Waiver intelligence (feature 9)**
```
BACKEND: use the _freeAgents block. Score each candidate on opportunity
(realistic playing time), availability (how widely rostered), league fit (does
THIS team need him), competition (which teams likely target him).

UI: ranked priority list with reasoning attached to each name. Not a generic
top-100 — every entry says why it matters for this specific roster.
```

**Weekly recap (feature 15)**
```
BACKEND: cron each Tuesday, pushed rather than waiting for a visit. Biggest
winner and loser, luckiest and unluckiest team (compare each score against
every other team's score that week, not just their actual opponent),
performance of the week, biggest waiver impact.

UI: a scannable recap page, shareable. This is the retention feature — it
should be worth opening on a Tuesday morning.
```

---

## Phase 10 — Beat My League

*Covers feature 7.*

```
Read design-system/MASTER.md.

BACKEND: for every team — title probability, structural strengths, weaknesses,
playoff schedule difficulty. Then for the user's team: biggest threat and why,
where they hold a real advantage, and league-specific strategy such as which
positions NOT to help a rival fix through a trade.

This must be specific to this league's actual rosters. Generic fantasy advice
is a failure condition.

UI — use the ui-ux-pro-max skill: threat cards per rival, with the user's
advantage and the strategic note surfaced clearly. Comparative Analysis
Dashboard patterns fit here.
```

**Commit:** `Analysis: beat my league, full-stack`

---

## Phase 11 — League History and manager ratings

*Covers feature 13. Carries ingest work: pre-2018 seasons use
`/leagueHistory/{league_id}?seasonId={season}`.*

```
Read /ingest, scripts/fetch_fixture.py, and design-system/MASTER.md.

BACKEND: extend ingest to pull prior seasons including the leagueHistory
endpoint. Scrub and store as with current-season fixtures.

Then: best and worst drafts, best waiver pickup, best and worst trades, biggest
comeback and collapse, highest weekly score, championships.

Then all-time manager ratings decomposed into draft skill, waiver skill, trade
skill, lineup skill, and schedule luck. Separating results from luck is the
entire point — someone with three titles and bad process should rank below
someone with one title and good process, and the ratings must show why.

UI — use the ui-ux-pro-max skill: league record book plus an all-time manager
table where the skill decomposition is visible, not just a final number. The
decomposition IS the feature; a single rating without it is just a leaderboard.
```

**Commit:** `History: league archive and manager skill ratings, full-stack`

---

## Phase 12 — Entertainment

*Covers feature 16.*

```
Read design-system/MASTER.md.

Weekly awards (team of the week, worst start/sit, luckiest, unluckiest, biggest
riser and faller) and a power ranking roast.

All derived from real computed results — the joke lands because the underlying
stat is true.

UI — use the ui-ux-pro-max skill: award cards designed to be screenshotted and
dropped into a group chat. Shareable image export.

Keep it good-natured. These go to the user's actual friends.
```

**Commit:** `Fun: weekly awards and roasts`

---

## Phase 13 — AI analyst

*Covers features 6 and 17. Build last.*

```
Read design-system/MASTER.md.

BACKEND: tool-calling against our own analytics endpoints — get_team_odds,
get_trade_impact, get_roster_weaknesses, get_waiver_targets,
get_playoff_outlook, get_league_threats.

The model interprets and narrates. It does NOT compute. Every number in a
response comes from a tool result. If a tool returns no data, the model says so
rather than estimating.

Handles: why am I projected to lose, what is my biggest weakness, who is my
biggest threat, should I trade with X, what should I target on waivers.

UI — use the ui-ux-pro-max skill: chat interface that can render the numbers it
cites as inline components, not just prose. When the analyst mentions a
probability, show it.
```

**Commit:** `AI: league analyst over analytics tools`

---

## Deferred by choice

**Draft replay** *(part of feature 4)*. Replaying a counterfactual draft means
modelling how the other eleven managers would have picked against a changed board
— an ADP-based behavioural model with positional-need logic and noise. A project
of its own. Phase 7 delivers the cheap honest version: what you passed on.

**Fantasy Lab** *(feature 14)*. The best long-term differentiator, but it
publishes claims derived from the simulator. Do not ship it until the sim has been
validated against a completed season — publishing confident findings from an
unvalidated model is worse than publishing nothing. Revisit after week 17.

**Own projection models.** Use ESPN's. Building projections is a separate
multi-month effort and is not what makes this app interesting.

## Timing note

NFL drafts cluster in late August. Nothing here is draft-day tooling, so there is
no hard deadline — draft data stays available all season and Phase 7 works
whenever you reach it. It is simply more fun in September than in November.