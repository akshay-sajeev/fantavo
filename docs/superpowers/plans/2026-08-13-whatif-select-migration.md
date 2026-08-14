# What-If Select Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three remaining raw native `<select>` elements in the what-if feature with the shared `Select` primitive, via a small shared `TeamSelect` component — per `docs/superpowers/specs/2026-08-13-whatif-select-migration-design.md`.

**Architecture:** Extend the shared `Select` primitive so consumers can control popup positioning (the what-if triggers are full-width card titles that must not be overlaid), then introduce one `TeamSelect` component owning the team-list mapping, id→name label resolution, and null guard, and point all three what-if call sites at it. Existing state-reset handlers are passed through unchanged, not reimplemented.

**Tech Stack:** Next.js 15 (App Router), TypeScript, Tailwind CSS v4, `@base-ui/react` (via the existing `components/ui/*` wrapper conventions). No new dependencies.

## Global Constraints

- No changes to `/sim`, `/ingest`, `/db`, or any page other than the what-if feature and the one-line null-guard on the dashboard's week picker.
- No changes to what-if simulation logic, request shapes, API routes, or result rendering — this swaps an input control and nothing else.
- Preserve verbatim: the three `aria-label` strings (`"Team to swap"`, `"Team sending players (side A)"`, `"Team sending players (side B)"`), and the existing `selectTeam` / `selectTeamA` / `selectTeamB` handlers, which clear player selections and reset `state` to `{ status: "idle" }`.
- No new global CSS tokens; reuse the already-styled `Select` primitive.
- The new component is named `TeamSelect`, **not** `TeamPicker` — `team-picker.tsx` files already exist in `analyst/`, `beat-my-league/`, `lineup/`, and `waivers/` for URL-driven server-navigated link lists, a different interaction. Do not touch those files.
- **Verification for every task**: `cd web && npx tsc --noEmit`, `npx eslint .`, `npm run build` — all clean. (`/web` has no unit-test harness in this repo; this is the established verification standard for every prior phase.) The sim API for live checks runs from the **repo root** (not `web/`) with `uvicorn sim.api.app:app --host 127.0.0.1 --port 8123`; the real fixture league id is `885686492`.
- If you run `npm run build` and then want `npm run dev` in the same session, `rm -rf web/.next` first — a shared `.next` directory between the two produces spurious webpack module-resolution errors unrelated to any code change.

---

## File Structure

Modified:
- `web/components/ui/select.tsx` — `SelectContent` gains forwarded positioner props and an anchor-width floor.
- `web/components/dashboard/week-matchups-card.tsx` — one handler line, replacing an unsafe cast with a null guard.
- `web/components/whatif/roster-swap-builder.tsx` — one raw `<select>` → `<TeamSelect>`.
- `web/components/whatif/trade-builder.tsx` — two raw `<select>` → `<TeamSelect>`.

Created:
- `web/components/whatif/team-select.tsx` — the shared team dropdown.

Not touched: the link-based `team-picker.tsx` components; every other file in `web/components/whatif/` (`player-picker.tsx`, `outcome-comparison.tsx`, `season-replay-panel.tsx`).

---

### Task 1: Extend `SelectContent`; null-guard the existing week picker

**Files:**
- Modify: `web/components/ui/select.tsx`
- Modify: `web/components/dashboard/week-matchups-card.tsx`

**Interfaces:**
- Produces: `SelectContent` accepting four additional optional props — `side`, `sideOffset` (default `4`), `align`, `alignItemWithTrigger` — forwarded to `SelectPrimitive.Positioner`. Task 2 consumes `alignItemWithTrigger`. All existing `SelectContent` call sites keep working unchanged (passing none of these leaves base-ui's own defaults in force, because an explicitly-passed `undefined` still triggers a destructuring default).

- [ ] **Step 1: Widen `SelectContent`'s props and forward them**

In `web/components/ui/select.tsx`, find:

```tsx
function SelectContent({ className, children, ...props }: SelectPrimitive.Popup.Props) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Positioner sideOffset={4} className="isolate z-50">
```

Replace with:

```tsx
function SelectContent({
  className,
  children,
  side,
  sideOffset = 4,
  align,
  alignItemWithTrigger,
  ...props
}: SelectPrimitive.Popup.Props &
  Pick<
    SelectPrimitive.Positioner.Props,
    "align" | "alignItemWithTrigger" | "side" | "sideOffset"
  >) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Positioner
        align={align}
        alignItemWithTrigger={alignItemWithTrigger}
        side={side}
        sideOffset={sideOffset}
        className="isolate z-50"
      >
```

This mirrors the identical `Pick<...Positioner.Props, ...>` pattern already used by `TooltipContent` in `web/components/ui/tooltip.tsx:36-49`.

- [ ] **Step 2: Give the popup a trigger-width floor**

Still in `web/components/ui/select.tsx`, in the `SelectPrimitive.Popup`'s `cn(...)` call, find:

```
"max-h-64 overflow-y-auto rounded-lg border border-border/70 bg-popover/95 p-1 text-popover-foreground shadow-xl backdrop-blur-md
```

Replace that leading run of classes with:

```
"max-h-64 min-w-[var(--anchor-width)] overflow-y-auto rounded-lg border border-border/70 bg-popover/95 p-1 text-popover-foreground shadow-xl backdrop-blur-md
```

(Leave the rest of the class string — the `data-[open]:` / `data-[closed]:` animation classes — exactly as it is. `--anchor-width` is set on the positioner by base-ui; see `select/positioner/SelectPositionerCssVars.mjs`.)

- [ ] **Step 3: Null-guard the week picker's handler**

In `web/components/dashboard/week-matchups-card.tsx`, find:

```tsx
        <Select
          value={selectedWeek}
          onValueChange={(value) => setSelectedWeek(value as number)}
        >
```

Replace with:

```tsx
        <Select
          value={selectedWeek}
          onValueChange={(value) => {
            if (value != null) setSelectedWeek(value);
          }}
        >
```

base-ui types single-select's callback value as `Value | null`; the guard narrows it to `number` and removes the unsafe cast. Nothing else in this file changes.

- [ ] **Step 4: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build`
Expected: all three clean.

Live check (the dashboard week picker is the only existing `SelectContent` consumer, so this confirms the primitive change didn't regress it): start the sim API from the repo root (`uvicorn sim.api.app:app --host 127.0.0.1 --port 8123`) and the Next dev server, load `/league/885686492`, open the "Matchups" card's week dropdown, confirm it still opens and still lists every week, and confirm selecting a different week still swaps the matchup list. Its popup will still overlay its trigger (base-ui's default is unchanged) — that is expected and correct at this step.

- [ ] **Step 5: Commit**

```bash
git add web/components/ui/select.tsx web/components/dashboard/week-matchups-card.tsx
git commit -m "Web: SelectContent forwards positioner props; null-guard week picker"
```

---

### Task 2: `TeamSelect` shared component

**Files:**
- Create: `web/components/whatif/team-select.tsx`

**Interfaces:**
- Consumes: `SelectContent`'s `alignItemWithTrigger` prop (Task 1); `Select`, `SelectTrigger`, `SelectValue`, `SelectItem` from `@/components/ui/select` (already exported); `cn` from `@/lib/utils`; the `TeamRoster` type from `@/lib/types`.
- Produces: `TeamSelect({ teams, value, onChange, label, className })` where `teams: TeamRoster[]`, `value: number`, `onChange: (teamId: number) => void`, `label: string`, `className?: string`. Task 3 renders it three times.

- [ ] **Step 1: Create the file**

```tsx
"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { TeamRoster } from "@/lib/types";

/**
 * Shared team dropdown for the what-if builders (trade + roster swap),
 * replacing the raw <select> elements those files used before the shared
 * Select primitive existed. Owns the three things all its call sites would
 * otherwise duplicate: the team -> item mapping, resolving the current
 * team_id back to a team_name for the trigger label, and guarding
 * base-ui's nullable single-select callback so `onChange` only ever fires
 * with a real team id.
 *
 * `alignItemWithTrigger={false}` because these triggers are full-width card
 * titles: base-ui's default aligns the selected item over the trigger,
 * which would cover the card header. Dropping below reads correctly here.
 *
 * Named TeamSelect, not TeamPicker -- the `team-picker.tsx` files in
 * analyst/, beat-my-league/, lineup/ and waivers/ are URL-driven
 * server-navigated link lists, a different interaction entirely.
 */
export function TeamSelect({
  teams,
  value,
  onChange,
  label,
  className,
}: {
  teams: TeamRoster[];
  value: number;
  onChange: (teamId: number) => void;
  label: string;
  className?: string;
}) {
  return (
    <Select
      value={value}
      onValueChange={(next) => {
        if (next != null) onChange(next);
      }}
    >
      <SelectTrigger aria-label={label} className={cn("font-semibold", className)}>
        <SelectValue>
          {(current: number) => teams.find((t) => t.team_id === current)?.team_name ?? ""}
        </SelectValue>
      </SelectTrigger>
      <SelectContent alignItemWithTrigger={false}>
        {teams.map((t) => (
          <SelectItem key={t.team_id} value={t.team_id}>
            {t.team_name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
```

`"use client"` is explicit here even though both of this component's importers already carry it — the component renders client-only base-ui primitives, so the directive documents that constraint at the file that actually depends on it.

- [ ] **Step 2: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build`
Expected: all three clean. (Nothing imports this file yet, so this only confirms it compiles standalone.)

- [ ] **Step 3: Commit**

```bash
git add web/components/whatif/team-select.tsx
git commit -m "Web: shared TeamSelect for the what-if builders"
```

---

### Task 3: Migrate the three what-if call sites

**Files:**
- Modify: `web/components/whatif/roster-swap-builder.tsx`
- Modify: `web/components/whatif/trade-builder.tsx`

**Interfaces:**
- Consumes: `TeamSelect({ teams, value, onChange, label, className })` (Task 2).
- Produces: no new exports. `RosterSwapBuilder({ teams, leagueId })` and `TradeBuilder({ teams, leagueId })` keep their existing signatures.

- [ ] **Step 1: Migrate `roster-swap-builder.tsx`**

Add the import alongside the existing ones:

```tsx
import { TeamSelect } from "@/components/whatif/team-select";
```

Then find:

```tsx
            <select
              value={teamId}
              onChange={(e) => selectTeam(Number(e.target.value))}
              className="w-full max-w-xs cursor-pointer rounded-md border border-border bg-background px-2 py-1.5 text-sm font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
              aria-label="Team to swap"
            >
              {teams.map((t) => (
                <option key={t.team_id} value={t.team_id}>
                  {t.team_name}
                </option>
              ))}
            </select>
```

Replace with:

```tsx
            <TeamSelect
              teams={teams}
              value={teamId}
              onChange={selectTeam}
              label="Team to swap"
              className="w-full max-w-xs"
            />
```

Nothing else in this file changes — `selectTeam` itself, `toggleBench`, `toggleStart`, `runSwap`, and every rendered child stay exactly as they are.

- [ ] **Step 2: Migrate `trade-builder.tsx`'s side A**

Add the import alongside the existing ones:

```tsx
import { TeamSelect } from "@/components/whatif/team-select";
```

Then find:

```tsx
              <select
                value={teamAId}
                onChange={(e) => selectTeamA(Number(e.target.value))}
                className="w-full cursor-pointer rounded-md border border-border bg-background px-2 py-1.5 text-sm font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
                aria-label="Team sending players (side A)"
              >
                {teams.map((t) => (
                  <option key={t.team_id} value={t.team_id}>
                    {t.team_name}
                  </option>
                ))}
              </select>
```

Replace with:

```tsx
              <TeamSelect
                teams={teams}
                value={teamAId}
                onChange={selectTeamA}
                label="Team sending players (side A)"
                className="w-full"
              />
```

- [ ] **Step 3: Migrate `trade-builder.tsx`'s side B**

Still in `web/components/whatif/trade-builder.tsx`, find:

```tsx
              <select
                value={teamBId}
                onChange={(e) => selectTeamB(Number(e.target.value))}
                className="w-full cursor-pointer rounded-md border border-border bg-background px-2 py-1.5 text-sm font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
                aria-label="Team sending players (side B)"
              >
                {teams.map((t) => (
                  <option key={t.team_id} value={t.team_id}>
                    {t.team_name}
                  </option>
                ))}
              </select>
```

Replace with:

```tsx
              <TeamSelect
                teams={teams}
                value={teamBId}
                onChange={selectTeamB}
                label="Team sending players (side B)"
                className="w-full"
              />
```

Nothing else in `trade-builder.tsx` changes — `selectTeamA`/`selectTeamB`, `toggleA`/`toggleB`, the `summary` memo, `runTrade`, the `sameTeam`/`countsMatch` validation messages, and every rendered child stay exactly as they are.

- [ ] **Step 4: Confirm no raw selects remain**

Run: `grep -rn "<select" web/components web/app`
Expected: no output.

- [ ] **Step 5: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build`
Expected: all three clean.

Live check: with the sim API running from the repo root and the Next dev server up, load `/league/885686492/whatif`. On the trade builder, confirm both dropdowns render the current team's name, open **below** their trigger (not covering the card title), span the trigger's width, and list every team. Pick a different team on side A and confirm its player list swaps and any previously-shown result clears. Do the same on the roster-swap tab. Then run one real trade and one real roster swap end-to-end and confirm before/after numbers still render.

- [ ] **Step 6: Commit**

```bash
git add web/components/whatif/roster-swap-builder.tsx web/components/whatif/trade-builder.tsx
git commit -m "Web: migrate what-if team dropdowns to the Select primitive"
```

---

### Task 4: Final end-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Full clean build**

```bash
cd web
npx tsc --noEmit
npx eslint .
npm run build
```

All three clean.

- [ ] **Step 2: Keyboard operability**

With the dev server and sim API running, on `/league/885686492/whatif`: Tab to a team dropdown, open it with Enter (and separately Space), navigate options with the arrow keys, select with Enter, and dismiss a fresh one with Escape. Confirm the trigger shows a visible focus ring throughout and that focus returns to the trigger after selecting. Repeat once on the roster-swap tab's dropdown.

- [ ] **Step 3: Responsive check**

Resize to 375px and reload `/league/885686492/whatif`. Confirm `document.documentElement.scrollWidth === document.documentElement.clientWidth` (no page-level horizontal scroll), and that the dropdowns still open and are usable at that width.

- [ ] **Step 4: Regression check on the dashboard**

Load `/league/885686492` and confirm the week picker still opens, lists every week, and switches the shown matchups — the one pre-existing `Select` consumer, re-checked after Task 1's primitive change.

- [ ] **Step 5: Console check**

Confirm no console errors on either page during any of the above. (A `webpack-hmr` WebSocket warning from the Next dev server is a known preview-proxy artifact, not an app error.)

- [ ] **Step 6: Final commit (only if fixes were needed)**

If Steps 1–5 surfaced anything, fix and commit it with a descriptive message. If everything passed as implemented, no further commit is needed — the work is already committed task-by-task.
