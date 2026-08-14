# Dashboard Layout & Visual Hierarchy Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the League Dashboard's matchup-row alignment bug, declutter Standings, turn Rosters into useful cards, and replace the duplicate "Remaining schedule" card with a week selector — per `docs/superpowers/specs/2026-08-13-dashboard-layout-cleanup-design.md`.

**Architecture:** Extract one shared `MatchupRow` component (fixed 3-column grid, truncation) used by both the existing hero matchup card and a new week-selector card, so matchup rows render identically everywhere and can never misalign again. Standings and Rosters get targeted, single-file visual changes. The week selector needs one new shared primitive (`ui/select.tsx`, wrapping `@base-ui/react/select` — this codebase's first `Select`) and one new Client Component that replaces `RemainingScheduleTable` entirely.

**Tech Stack:** Next.js 15 (App Router), TypeScript, Tailwind CSS v4, `@base-ui/react` (via existing `ui/*` wrapper conventions), `lucide-react`. No new dependencies.

## Global Constraints

- No changes to `/sim`, `/ingest`, `/db`, or any page other than the league dashboard (`web/app/league/[leagueId]/page.tsx` and `web/components/dashboard/*`).
- **No per-matchup win probability** — no such data exists in the API. Matchup rows show a plain "VS" pill, never a percentage.
- **No "Roster Grade"** — no such data exists in the API. Roster cards show Star Player and Proj. Points only.
- Star Player (max) and Proj. Points (sum) are computed only via simple selection/summation over `RosterPlayer.mean` values already present in the already-fetched `RosterResponse` — no new fetch, no new modeled number, no new backend work.
- Reuses the Dark Glass Makeover's existing tokens and primitives (`--shadow-glow-primary`, `Card`, `Badge`, `Tooltip`, etc.) — no new global CSS tokens.
- No emoji icons — `lucide-react` only, matching this project's existing anti-pattern rule.
- **Verification for every task**: `cd web && npx tsc --noEmit`, `npx eslint .`, `npm run build`, plus a real-browser check via the dev server (this repo has no unit-test harness for `/web`, confirmed in the prior Dark Glass Makeover phase). The sim API can be run locally for real data: `uvicorn sim.api.app:app --host 127.0.0.1 --port 8123` from the repo root (no Postgres required against the checked-in fixture league).

---

## File Structure

New:
- `web/components/dashboard/matchup-row.tsx` — shared matchup-row layout (fixes the alignment bug), used by both the hero card and the new week selector.
- `web/components/ui/select.tsx` — new shared primitive wrapping `@base-ui/react/select`, following this codebase's existing `ui/*` composition style (`tabs.tsx`, `tooltip.tsx`). **This is the first `Select` in the codebase** — its open/closed state data-attributes are inferred by analogy with `tooltip.tsx`'s already-verified attribute names, since `@base-ui/react/select`'s own type declarations don't enumerate a data-attribute name constant the way `@base-ui/react/tooltip` does. The task below requires a live-browser check of the open/closed visual states specifically, with instructions to correct the selectors if the runtime attributes differ from what's written.
- `web/components/dashboard/week-matchups-card.tsx` — replaces `remaining-schedule-table.tsx`: a week-select dropdown (any week, not just remaining ones) rendering that week's matchups via `MatchupRow`.

Modified:
- `web/components/dashboard/current-matchup-card.tsx` — matchup list now renders via `MatchupRow`.
- `web/components/dashboard/standings-table.tsx` — banner replaced with a compact tooltip pill; top-3 rows get rank badges.
- `web/components/dashboard/rosters-grid.tsx` — cards gain Star Player / Proj. Points, and a restyled "View Full Roster" disclosure control.
- `web/app/league/[leagueId]/page.tsx` — swaps `RemainingScheduleTable` for `WeekMatchupsCard`.

Deleted:
- `web/components/dashboard/remaining-schedule-table.tsx` — fully replaced by `week-matchups-card.tsx`.

Not modified: the outer dashboard grid (`grid grid-cols-1 lg:grid-cols-3 gap-4` with Standings at `lg:col-span-2`) — confirmed during brainstorming to already match the requested structure.

---

### Task 1: `MatchupRow` shared component + `CurrentMatchupCard` integration

**Files:**
- Create: `web/components/dashboard/matchup-row.tsx`
- Modify: `web/components/dashboard/current-matchup-card.tsx`

**Interfaces:**
- Produces: `MatchupRow({ homeTeamName, awayTeamName }: { homeTeamName: string | null; awayTeamName: string | null }): JSX.Element` — used by this task's `CurrentMatchupCard` update and by Task 4's `WeekMatchupsCard`.

- [ ] **Step 1: Create `matchup-row.tsx`**

```tsx
import { Badge } from "@/components/ui/badge";

/**
 * Shared row layout for a single matchup, used by both the dashboard's
 * current-matchup hero card and the week selector -- a fixed
 * grid-cols-[1fr_auto_1fr] with truncation on each team name means a long
 * name can never push the centered "VS" pill out of alignment (the bug this
 * fixes), since the middle column's width never depends on its neighbors'
 * content length.
 */
export function MatchupRow({
  homeTeamName,
  awayTeamName,
}: {
  homeTeamName: string | null;
  awayTeamName: string | null;
}) {
  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
      <span className="min-w-0 truncate text-left font-medium">{homeTeamName ?? "TBD"}</span>
      <Badge variant="secondary" className="shrink-0">
        VS
      </Badge>
      <span className="min-w-0 truncate text-right font-medium">{awayTeamName ?? "TBD"}</span>
    </div>
  );
}
```

- [ ] **Step 2: Use it in `CurrentMatchupCard`**

In `web/components/dashboard/current-matchup-card.tsx`, add the import alongside the existing ones:

```tsx
import { MatchupRow } from "@/components/dashboard/matchup-row";
```

Replace:

```tsx
            <ul className="divide-y divide-border">
              {matchups.map((m) => (
                <li
                  key={`${m.home_team_id}-${m.away_team_id}`}
                  className="flex items-center justify-between gap-3 py-2 text-sm"
                >
                  <span className="font-medium">{m.home_team_name ?? "TBD"}</span>
                  <span className="text-xs text-muted-foreground">vs</span>
                  <span className="text-right font-medium">{m.away_team_name ?? "TBD"}</span>
                </li>
              ))}
            </ul>
```

with:

```tsx
            <ul className="divide-y divide-border">
              {matchups.map((m) => (
                <li key={`${m.home_team_id}-${m.away_team_id}`} className="py-2 text-sm">
                  <MatchupRow homeTeamName={m.home_team_name} awayTeamName={m.away_team_name} />
                </li>
              ))}
            </ul>
```

Nothing else in this file changes (the `week`/`matchups`/season-complete logic, `TiltCard` wrapper, and "Upcoming" badge stay exactly as-is).

- [ ] **Step 3: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build` — all clean.

Start the sim API (`uvicorn sim.api.app:app --host 127.0.0.1 --port 8123` from repo root) and the Next dev server, load `/league/<a real league id, e.g. 885686492>`. Confirm the hero matchup card's rows show `Home Team ... VS ... Away Team` with the VS pill centered. If any team name in the fixture data is long enough to test truncation, confirm it truncates with an ellipsis rather than pushing the pill; if not, temporarily shrink the browser width to force truncation and confirm the pill stays centered, then restore the width (no code change needed either way — this is a visual check only).

- [ ] **Step 4: Commit**

```bash
git add web/components/dashboard/matchup-row.tsx web/components/dashboard/current-matchup-card.tsx
git commit -m "Web: shared MatchupRow layout, fixes VS alignment bug"
```

---

### Task 2: Standings — compact info pill + rank badges

**Files:**
- Modify: `web/components/dashboard/standings-table.tsx`

**Interfaces:**
- Consumes: `Tooltip`, `TooltipContent`, `TooltipTrigger` (`web/components/ui/tooltip.tsx`, already wired to a `TooltipProvider` in `web/app/layout.tsx`).
- No change to `StandingsTable`'s exported signature (`{ simulation, schedule }`).

- [ ] **Step 1: Add the Tooltip import**

In `web/components/dashboard/standings-table.tsx`, add to the existing imports:

```tsx
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
```

- [ ] **Step 2: Replace the banner with a compact pill**

Replace:

```tsx
      {gamesPlayed === 0 ? (
        <div className="flex items-start gap-2 rounded-lg border border-border bg-muted/60 p-3 text-sm text-muted-foreground">
          <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <p>
            No games have been played yet this season
            {schedule.current_week ? ` — Week ${schedule.current_week} is upcoming` : ""}. The
            standings below are simulated projections ({simulation.n_sims.toLocaleString()}{" "}
            simulated seasons), not an actual win-loss record.
          </p>
        </div>
      ) : null}
```

with:

```tsx
      {gamesPlayed === 0 ? (
        <Tooltip>
          <TooltipTrigger
            render={
              <span className="inline-flex w-fit cursor-default items-center gap-1.5 rounded-full border border-border/70 bg-muted/60 px-2.5 py-1 text-xs text-muted-foreground" />
            }
          >
            <Info className="h-3.5 w-3.5" aria-hidden="true" />
            {simulation.n_sims.toLocaleString()} simulations
          </TooltipTrigger>
          <TooltipContent side="bottom">
            No games have been played yet this season
            {schedule.current_week ? ` — Week ${schedule.current_week} is upcoming` : ""}. The
            standings below are simulated projections, not an actual win-loss record.
          </TooltipContent>
        </Tooltip>
      ) : null}
```

(`Info` stays imported from `lucide-react` — it's still used, just inside the pill now instead of the old banner.)

- [ ] **Step 3: Add rank badges for the top 3 rows**

Replace:

```tsx
                  <TableCell className="tabular-nums text-muted-foreground">
                    {projectedRank + 1}
                  </TableCell>
```

with:

```tsx
                  <TableCell className="tabular-nums text-muted-foreground">
                    {projectedRank < 3 ? (
                      <span
                        className={cn(
                          "inline-flex h-6 w-6 items-center justify-center rounded-full border text-xs font-bold",
                          projectedRank === 0 &&
                            "border-[#FFD700]/40 bg-[#FFD700]/15 text-[#FFD700]",
                          projectedRank === 1 &&
                            "border-[#C0C0C0]/40 bg-[#C0C0C0]/15 text-[#C0C0C0]",
                          projectedRank === 2 &&
                            "border-[#CD7A2D]/40 bg-[#CD7A2D]/15 text-[#CD7A2D]"
                        )}
                      >
                        {projectedRank + 1}
                      </span>
                    ) : (
                      projectedRank + 1
                    )}
                  </TableCell>
```

(`cn` is already imported in this file from `@/lib/utils`.)

- [ ] **Step 4: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build` — all clean.

In the dev server (sim API running, per Task 1's verification), load the dashboard: confirm the banner is gone, replaced by a small pill reading "N simulations"; hover/focus it and confirm the full original sentence appears in a tooltip. Confirm rows 1/2/3 show gold/silver/bronze circular badges and rows 4+ show plain numbers. Confirm text in each badge is legible (light badge color on the dark card background).

- [ ] **Step 5: Commit**

```bash
git add web/components/dashboard/standings-table.tsx
git commit -m "Web: compact info tooltip + rank badges on Standings"
```

---

### Task 3: Rosters — Star Player, Proj. Points, restyled disclosure

**Files:**
- Modify: `web/components/dashboard/rosters-grid.tsx`

**Interfaces:**
- Consumes: `formatPoints` (`web/lib/format.ts`, already exists, unchanged), `RosterPlayer`/`TeamRoster` types (`web/lib/types.ts`, unchanged).
- No change to `RostersGrid`'s exported signature (`{ teams }`). Stays a Server Component — no new client interactivity, `<details>`/`<summary>` still does the disclosure work with no JS.

- [ ] **Step 1: Replace the file**

```tsx
import { ChevronDown } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatPoints } from "@/lib/format";
import type { RosterPlayer, TeamRoster } from "@/lib/types";

/**
 * Compact per-team roster listing for the dashboard overview. Star Player
 * (highest-projected starter) and Proj. Points (sum of starters' mean
 * projections) are simple selection/summation over per-player numbers the
 * roster API already returns -- never a new modeled number, and never a
 * "Roster Grade" (no such rating exists anywhere in this app's data model).
 * Availability/floor/ceiling/risk still live on the dedicated Risk panel
 * (/risk), not duplicated here.
 *
 * <summary> must be the <details> element's first child for correct native
 * disclosure semantics, so the always-visible team name/stats live in a
 * plain wrapper alongside (not inside) the <details> -- only the full
 * starters list is native-collapsible. Keyboard- and screen-reader-
 * accessible with no client JS needed, which matters since this whole page
 * is a Server Component.
 */
export function RostersGrid({ teams }: { teams: TeamRoster[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-base">Rosters</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {teams.map((team) => {
            const eligible = team.starters.filter(
              (p): p is RosterPlayer & { mean: number } => p.mean != null
            );
            const starPlayer =
              eligible.length > 0
                ? eligible.reduce((best, p) => (p.mean > best.mean ? p : best))
                : null;
            const projectedPoints = eligible.reduce((sum, p) => sum + p.mean, 0);

            return (
              <div
                key={team.team_id}
                className="rounded-lg border border-border/70 p-3 transition-all duration-150 hover:bg-primary/5 hover:shadow-[var(--shadow-glow-primary)]"
              >
                <div className="space-y-2">
                  <p className="font-medium">{team.team_name}</p>
                  {starPlayer ? (
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">Star Player</span>
                      <span className="font-medium">
                        {starPlayer.name}{" "}
                        <span className="text-xs text-muted-foreground">
                          {starPlayer.position}
                        </span>
                      </span>
                    </div>
                  ) : null}
                  {eligible.length > 0 ? (
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">Proj. Points</span>
                      <span className="font-medium tabular-nums">
                        {formatPoints(projectedPoints)}
                      </span>
                    </div>
                  ) : null}
                </div>

                <details className="group mt-2">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-2 rounded-md border border-border/70 px-2 py-1 text-xs font-medium text-muted-foreground marker:hidden hover:bg-muted/60 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring">
                    View Full Roster
                    <ChevronDown
                      className="h-3.5 w-3.5 shrink-0 transition-transform duration-150 group-open:rotate-180"
                      aria-hidden="true"
                    />
                  </summary>
                  <ul className="mt-2 space-y-1 text-sm">
                    {team.starters.length === 0 ? (
                      <li className="text-muted-foreground">No roster ingested yet.</li>
                    ) : (
                      team.starters.map((p) => (
                        <li key={p.player_id} className="flex items-center justify-between gap-2">
                          <span>{p.name}</span>
                          <span className="shrink-0 text-xs text-muted-foreground">
                            {p.lineup_slot}
                          </span>
                        </li>
                      ))
                    )}
                  </ul>
                </details>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build` — all clean.

In the dev server, load the dashboard: confirm each roster card shows the team name, a "Star Player" row (name + position) and "Proj. Points" row (a number) above a "View Full Roster" control; click (and separately, tab-to-and-press-Enter-on) the control and confirm the full starters list expands/collapses, with the chevron rotating. Confirm hovering a card shows the glow (unchanged from before). If any team has zero starters with a usable projection (unlikely per the API's documented invariant, but check), confirm the card just omits the Star Player/Proj. Points rows rather than showing an incorrect `0`.

- [ ] **Step 3: Commit**

```bash
git add web/components/dashboard/rosters-grid.tsx
git commit -m "Web: interactive roster cards with Star Player and Proj. Points"
```

---

### Task 4: Week selector — `ui/select.tsx` primitive + `WeekMatchupsCard`

**Files:**
- Create: `web/components/ui/select.tsx`
- Create: `web/components/dashboard/week-matchups-card.tsx`
- Delete: `web/components/dashboard/remaining-schedule-table.tsx`
- Modify: `web/app/league/[leagueId]/page.tsx`

**Interfaces:**
- Consumes: `MatchupRow` (Task 1).
- Produces: `Select`, `SelectTrigger`, `SelectValue`, `SelectContent`, `SelectItem` (new primitives, generic over value type, mirroring `@base-ui/react/select`'s own `Root`/`Trigger`/`Value`/`Popup`/`Item` composition). `WeekMatchupsCard({ schedule }: { schedule: ScheduleResponse }): JSX.Element` — same prop shape `RemainingScheduleTable` had, so `page.tsx`'s change is a drop-in swap.

- [ ] **Step 1: Create `web/components/ui/select.tsx`**

```tsx
"use client"

import { Select as SelectPrimitive } from "@base-ui/react/select"
import { Check, ChevronDown } from "lucide-react"

import { cn } from "@/lib/utils"

function Select<Value, Multiple extends boolean | undefined = false>(
  props: SelectPrimitive.Root.Props<Value, Multiple>
) {
  return <SelectPrimitive.Root data-slot="select" {...props} />
}

function SelectTrigger({ className, children, ...props }: SelectPrimitive.Trigger.Props) {
  return (
    <SelectPrimitive.Trigger
      data-slot="select-trigger"
      className={cn(
        "flex h-8 w-fit cursor-pointer items-center justify-between gap-2 rounded-lg border border-border/70 bg-card/70 px-3 text-sm text-foreground backdrop-blur-md transition-all outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 data-[popup-open]:shadow-[var(--shadow-glow-primary)]",
        className
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon data-slot="select-icon">
        <ChevronDown className="h-4 w-4 opacity-60" aria-hidden="true" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  )
}

function SelectValue(props: SelectPrimitive.Value.Props) {
  return <SelectPrimitive.Value data-slot="select-value" {...props} />
}

function SelectContent({ className, children, ...props }: SelectPrimitive.Popup.Props) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Positioner sideOffset={4} className="isolate z-50">
        <SelectPrimitive.Popup
          data-slot="select-content"
          className={cn(
            "max-h-64 overflow-y-auto rounded-lg border border-border/70 bg-popover/95 p-1 text-popover-foreground shadow-xl backdrop-blur-md data-[open]:animate-in data-[open]:fade-in-0 data-[open]:zoom-in-95 data-[closed]:animate-out data-[closed]:fade-out-0 data-[closed]:zoom-out-95",
            className
          )}
          {...props}
        >
          <SelectPrimitive.List>{children}</SelectPrimitive.List>
        </SelectPrimitive.Popup>
      </SelectPrimitive.Positioner>
    </SelectPrimitive.Portal>
  )
}

function SelectItem({ className, children, ...props }: SelectPrimitive.Item.Props) {
  return (
    <SelectPrimitive.Item
      data-slot="select-item"
      className={cn(
        "relative flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none select-none data-[highlighted]:bg-primary/10 data-[highlighted]:text-foreground",
        className
      )}
      {...props}
    >
      <SelectPrimitive.ItemText data-slot="select-item-text">{children}</SelectPrimitive.ItemText>
      <SelectPrimitive.ItemIndicator data-slot="select-item-indicator" className="ml-auto">
        <Check className="h-4 w-4 text-primary" aria-hidden="true" />
      </SelectPrimitive.ItemIndicator>
    </SelectPrimitive.Item>
  )
}

export { Select, SelectTrigger, SelectValue, SelectContent, SelectItem }
```

**Important — this is the first `Select` in the codebase, so its open/closed data-attribute selectors (`data-[popup-open]` on the trigger, `data-[open]`/`data-[closed]` on the popup) are inferred by analogy with `web/components/ui/tooltip.tsx`'s already-verified attribute names (`@base-ui/react/select`'s type declarations don't enumerate a data-attribute constant the way `@base-ui/react/tooltip` does). Step 4's live-browser check specifically verifies these actually apply — if the trigger's glow or the popup's open/close animation don't visually trigger, inspect the real DOM attribute names (open devtools, open the select, look at what `data-*` attributes actually appear on the trigger and popup elements) and correct the class selectors above to match.**

- [ ] **Step 2: Create `web/components/dashboard/week-matchups-card.tsx`**

```tsx
"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { MatchupRow } from "@/components/dashboard/matchup-row";
import type { ScheduleResponse } from "@/lib/types";

/**
 * Replaces the old "Remaining schedule" list with a week picker: any week
 * (not just upcoming ones) can be inspected, using the same MatchupRow
 * layout as the dashboard's current-matchup hero card so matchups render
 * identically everywhere on the page. Never fabricates a score -- like
 * CurrentMatchupCard, this only ever shows scheduled pairings, never a
 * result, since no actual weekly results are ingested yet (see
 * CurrentMatchupCard's docstring for the same rule).
 *
 * Defaults to schedule.current_week when one exists; when the season is
 * already fully decided (current_week is null), defaults to the last
 * regular-season week instead of an empty/past-the-end state, since this is
 * a "pick any week" browser now, not a "what's left" list.
 */
export function WeekMatchupsCard({ schedule }: { schedule: ScheduleResponse }) {
  const defaultWeek = schedule.current_week ?? schedule.n_regular_weeks;
  const [selectedWeek, setSelectedWeek] = useState(defaultWeek);
  const matchups = schedule.weeks[selectedWeek - 1] ?? [];

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="font-heading text-base">Matchups</CardTitle>
        <Select
          value={selectedWeek}
          onValueChange={(value) => setSelectedWeek(value as number)}
        >
          <SelectTrigger aria-label="Select week">
            <SelectValue>{(value: number) => `Week ${value}`}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {schedule.weeks.map((_, i) => (
              <SelectItem key={i + 1} value={i + 1}>
                Week {i + 1}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </CardHeader>
      <CardContent>
        {matchups.length === 0 ? (
          <p className="text-sm text-muted-foreground">No matchups scheduled for this week.</p>
        ) : (
          <ul className="divide-y divide-border">
            {matchups.map((m) => (
              <li key={`${m.home_team_id}-${m.away_team_id}`} className="py-2 text-sm">
                <MatchupRow homeTeamName={m.home_team_name} awayTeamName={m.away_team_name} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 3: Wire it into the page, delete the old file**

In `web/app/league/[leagueId]/page.tsx`, replace:

```tsx
import { RemainingScheduleTable } from "@/components/dashboard/remaining-schedule-table";
```

with:

```tsx
import { WeekMatchupsCard } from "@/components/dashboard/week-matchups-card";
```

and replace:

```tsx
        <RevealItem>
          <RemainingScheduleTable schedule={schedule} />
        </RevealItem>
```

with:

```tsx
        <RevealItem>
          <WeekMatchupsCard schedule={schedule} />
        </RevealItem>
```

Delete the now-unused file:

```bash
git rm web/components/dashboard/remaining-schedule-table.tsx
```

- [ ] **Step 4: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build` — all clean.

In the dev server (sim API running), load the dashboard:
- Confirm the card is titled "Matchups" with a week-select dropdown in its header, defaulting to the current/upcoming week.
- Click the dropdown: confirm it opens with a glass popup listing every week, confirm the trigger shows a glow while open (or note in the task report if it doesn't, and check/fix the data-attribute selectors per Step 1's note).
- Select a different week: confirm the matchup list below updates to that week's real matchups, rendered via the same `MatchupRow` layout (VS pill centered).
- Confirm keyboard operation works: Tab to the trigger, open with Enter/Space, navigate options with arrow keys, select with Enter, and confirm focus returns sensibly to the trigger.
- Confirm no console errors.
- Confirm `remaining-schedule-table.tsx` no longer exists and nothing else references it (`grep -r "RemainingScheduleTable" web/` returns nothing).

- [ ] **Step 5: Commit**

```bash
git add web/components/ui/select.tsx web/components/dashboard/week-matchups-card.tsx "web/app/league/[leagueId]/page.tsx"
git commit -m "Web: week-selector Select primitive, replaces Remaining schedule card"
```

---

### Task 5: Final end-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Full clean build**

```bash
cd web
npx tsc --noEmit
npx eslint .
npm run build
```

All three clean.

- [ ] **Step 2: Browser walkthrough with real data**

Start the sim API (`uvicorn sim.api.app:app --host 127.0.0.1 --port 8123` from repo root) and the Next dev server, load `/league/<a real league id>`:
- Standings: pill + tooltip render correctly, top-3 rank badges show, table alignment (Team left, numbers right) unchanged from before.
- Hero matchup card: rows use the fixed-grid `MatchupRow` layout, VS pill never drifts.
- Rosters: each card shows Star Player + Proj. Points, "View Full Roster" expands/collapses via mouse and keyboard.
- Matchups (week selector): opens, lists all weeks, switching weeks updates the shown matchups correctly.
- No console errors anywhere on the page.

- [ ] **Step 3: Mobile width check**

`resize_window` to the mobile preset (375px), reload the dashboard: confirm no page-level horizontal scroll (`document.documentElement.scrollWidth === document.documentElement.clientWidth`), confirm the week-select dropdown and roster cards remain usable at that width.

- [ ] **Step 4: Final commit (if any fixes were needed)**

If Steps 1–3 required fixes (e.g. correcting the `Select` data-attribute selectors from Task 4's note), commit them individually with descriptive messages. If everything passed as implemented, no further commit is needed.
