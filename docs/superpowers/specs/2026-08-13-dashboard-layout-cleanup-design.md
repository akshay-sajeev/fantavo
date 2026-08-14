# Dashboard Layout & Visual Hierarchy Cleanup — Design Spec

**Date:** 2026-08-13
**Status:** Approved, ready for implementation plan
**Scope:** `web/app/league/[leagueId]/page.tsx` and its dashboard components only (`web/components/dashboard/*`). No changes to `/sim`, `/ingest`, `/db`, other pages, or any simulation/analytics logic. Builds on the Dark Glass Makeover (`docs/superpowers/specs/2026-08-13-dark-glass-makeover-design.md`) — reuses its tokens/primitives, does not change them.

## Problem

Four concrete layout/hierarchy issues on the League Dashboard:
1. Matchup rows can visually break: team names have no width constraint, so a long name pushes the "vs" indicator off-center.
2. Standings leads with a large, text-heavy gray banner that competes with the actual data for attention.
3. "Rosters" only shows static "N starters" boxes — no useful info until expanded.
4. "Remaining schedule" duplicates the matchup list shown elsewhere in a second, separate card.

## Data-provenance constraints (resolved during brainstorming)

Two items in the original request would have required inventing data that doesn't exist anywhere in the sim API, which this project's rules forbid:
- **No per-matchup win probability exists.** The simulator produces season-long odds (title/playoff %), never a "Team A beats Team B this week" number. Decision: matchup rows show a plain "VS" pill, no percentage.
- **No "Roster Grade" (letter grade) exists.** Draft Autopsy grades draft *picks*; nothing scores overall roster strength. Decision: roster cards show Star Player and Proj. Points only (both real, derived by simple selection/sum over already-returned per-player `mean` projections) — no grade badge anywhere.

"Remaining schedule" is replaced with a week-selector dropdown (not deleted outright) so the ability to browse future weeks isn't lost.

## 1. Shared `MatchupRow` component (new)

**File:** `web/components/dashboard/matchup-row.tsx`

Extracted from `CurrentMatchupCard`'s row markup so both the hero card and the new week selector render matchups identically. Props: `{ homeTeamName: string | null; awayTeamName: string | null }`.

Layout: `grid grid-cols-[1fr_auto_1fr] items-center gap-2` per row —
- Column 1 (home team): `truncate text-left font-medium`, `min-w-0` so `truncate` actually applies inside a grid track.
- Column 2: a centered `Badge` (or plain styled span) reading "VS", fixed content so it never moves regardless of neighboring text length.
- Column 3 (away team): `truncate text-right font-medium`, `min-w-0`.

This directly fixes the alignment bug: the grid's `1fr`/`auto`/`1fr` tracks guarantee the middle column never shifts, and `truncate` (which requires `overflow-hidden`, `text-overflow-ellipsis`, and a constrained width — `min-w-0` provides that inside a grid track) keeps a long name from ever pushing past its column.

## 2. `CurrentMatchupCard` — reuse `MatchupRow`

**File:** `web/components/dashboard/current-matchup-card.tsx` (modify)

Replace the existing `<ul><li className="flex items-center justify-between...">` block with `<ul className="divide-y divide-border">{matchups.map(m => <li key={...}><MatchupRow homeTeamName={m.home_team_name} awayTeamName={m.away_team_name} /></li>)}</ul>`. No other change — the week/season-complete logic, the `TiltCard` wrapper, and the "Upcoming" badge are untouched.

## 3. Standings — compact info pill + rank badges

**File:** `web/components/dashboard/standings-table.tsx` (modify)

- The existing `gamesPlayed === 0` banner (`<div className="flex items-start gap-2 rounded-lg border ... p-3 ...">`) is replaced by a small `Tooltip`-wrapped pill next to a new `<CardAction>`-style header row: an `Info` icon (lucide, already imported) + `{simulation.n_sims.toLocaleString()} simulations` text, `Tooltip` content carrying the full original sentence ("No games have been played yet this season — Week N is upcoming. These are simulated projections, not an actual win-loss record."). The pill only renders the "simulations run" framing; the full explanatory sentence moves entirely into the tooltip so it's discoverable but not always taking up vertical space. This pill still only appears in the `gamesPlayed === 0` branch — once real results exist, nothing here needs to change (same as today).
- Rank badges: for `projectedRank` 0/1/2 (displayed rank 1/2/3), the numeral cell renders a small circular badge instead of a plain number — gold (`#projectedRank === 0`), silver (`=== 1`), bronze (`=== 2`), using the existing `--chart-3` (gold-adjacent) / neutral silver / a bronze-toned amber, NOT reusing `--brand-accent` (that stays reserved for the "rare, high-signal" uses per the Dark Glass Makeover spec — a rank badge is decorative, not a CTA). Ranks 4+ keep the plain numeral as today.
- Column alignment (Team left, numbers right) is already correct — no change.

## 4. Rosters — interactive cards

**File:** `web/components/dashboard/rosters-grid.tsx` (rewrite)

Each team's `<details>` block gains two new summary lines above the existing starters list, computed inline from already-fetched `TeamRoster.starters` (no new fetch, no new API):

```ts
const eligible = team.starters.filter(
  (p): p is RosterPlayer & { mean: number } => p.mean != null
);
const starPlayer = eligible.length > 0
  ? eligible.reduce((best, p) => (p.mean > best.mean ? p : best))
  : null;
const projectedPoints = eligible.reduce((sum, p) => sum + p.mean, 0);
```

Per `RosterPlayer`'s documented invariant ("`has_projection` is never false for a starter"), `eligible` should equal `team.starters` in practice — the filter is defensive, not an expected-to-trigger case. If `eligible` is empty (no usable projections at all), the card omits the Star Player/Proj. Points lines entirely rather than showing a misleading `0`.

Card content, top to bottom:
- Team name (existing).
- Star Player row: player name + position, only rendered when `starPlayer` is non-null.
- Proj. Points row: `formatPoints(projectedPoints)` (existing formatter from `lib/format.ts`), only rendered when `eligible.length > 0`.
- The existing `<summary>` becomes a styled "View Full Roster" control (chevron icon that rotates on `group-open`, matching the existing `group`/`group-open` Tailwind pattern already in this file) instead of the current bare "N starters" text — clicking it still expands the same native `<details>` starters list as today. No new client-side state; this stays a Server Component, keeping zero-JS accessibility.

No "Roster Grade" badge anywhere, per the resolved constraint above.

## 5. Week selector (replaces `RemainingScheduleTable`)

**New file:** `web/components/ui/select.tsx` — a new shared primitive (this codebase has no `Select` yet). Wraps `@base-ui/react/select` (already a transitive dependency of `@base-ui/react`, which every other `ui/*` primitive in this codebase already uses — e.g. `tabs.tsx`, `tooltip.tsx`) following this repo's existing composition style (`cn`, `data-slot`, dark-glass token classes: `bg-popover`, `border-border/70`, glow-ring on open, consistent with `tooltip.tsx`'s popup styling). Exports `Select`, `SelectTrigger`, `SelectValue`, `SelectContent`, `SelectItem` — the subset the week picker needs.

**New file:** `web/components/dashboard/week-matchups-card.tsx` (replaces `remaining-schedule-table.tsx`, which is deleted) — a Client Component (`"use client"`, needs `useState` for the selected week):
- `Select` defaulting to `schedule.current_week` (or the first week with matchups if `current_week` is null — mirrors `RemainingScheduleTable`'s existing `startWeek` fallback logic), listing every week `1..n_regular_weeks` as `SelectItem`s.
- Below the select, the chosen week's matchups render via the same `MatchupRow` component from item 1 — one shared row layout across the whole dashboard.
- Card title becomes "Matchups" (was "Remaining schedule") since it's no longer schedule-forward-only; a user can pick any week, not just upcoming ones.

**File:** `web/app/league/[leagueId]/page.tsx` (modify) — swap the `RemainingScheduleTable` import/usage for `WeekMatchupsCard`. No other structural change: the existing `Reveal`/`RevealItem`/grid wrapping stays exactly as-is (per the brainstorming conversation, the outer `grid grid-cols-1 lg:grid-cols-3 gap-4` with Standings at `lg:col-span-2` already matches the requested grid structure — confirmed, not rebuilt).

## Testing / verification

Same standard as the Dark Glass Makeover: `cd web && npx tsc --noEmit && npx eslint . && npm run build`, plus a real-browser check (dev server + live sim API against the real league) — confirm the VS pill stays centered with a long team name (visually or by shrinking the viewport), confirm the info tooltip shows the full sentence, confirm rank badges render for the top 3 only, confirm roster cards show Star Player/Proj. Points and the expand control still works via keyboard, confirm the week selector actually changes which week's matchups render, confirm no page-level horizontal scroll at 375px, confirm no console errors.

## Out of scope

- Roster Grade (explicitly rejected — no backing data; not deferred to a background task per the project owner's choice).
- Per-matchup win probability (explicitly rejected — no backing data).
- Avatar/icon images for teams (not requested beyond "Team Name & Avatar/Icon" in the original ask's carousel option, which was resolved to a plain card grid, not a carousel — no avatar images exist in this app's data model, so none are added).
- Any change to `/sim`, `/ingest`, `/db`, or pages other than the dashboard.
