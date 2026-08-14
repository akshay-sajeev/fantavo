# What-If Select Migration — Design Spec

**Date:** 2026-08-13
**Status:** Approved, ready for implementation plan
**Scope:** `web/components/whatif/*`, plus a targeted extension to `web/components/ui/select.tsx` and one line in `web/components/dashboard/week-matchups-card.tsx`. No changes to `/sim`, `/ingest`, `/db`, or any other page.

## Problem

`web/components/ui/select.tsx` was introduced in the dashboard layout cleanup phase (Phase 15) as this codebase's first `Select` primitive, but it shipped with exactly one consumer (the dashboard's week picker). Three raw native `<select>` elements remain in the what-if feature:

- `web/components/whatif/roster-swap-builder.tsx` — one team picker ("Team to swap").
- `web/components/whatif/trade-builder.tsx` — two team pickers ("Team sending players (side A)" / "(side B)").

All three are structurally identical: a `number` team-id value, an option per team, an `aria-label`, and an `onChange` that resets dependent selection state. They predate the primitive and are visually inconsistent with it (native control chrome against the dark-glass theme).

The final review of Phase 15 named this migration as the follow-up that would prove the primitive is genuinely reusable rather than fitted to one call site.

## 1. Extend `SelectContent` to forward positioner props

**File:** `web/components/ui/select.tsx` (modify)

`SelectContent` currently accepts only `SelectPrimitive.Popup.Props` and hardcodes `sideOffset={4}` on the positioner. Two problems this migration forces:

- base-ui's `SelectPositioner` defaults `alignItemWithTrigger = true`, which forces `side = 'none'` and positions the popup *overlaying* the trigger (native-macOS style). The hardcoded `sideOffset` is inert as a result. That reads acceptably for the narrow week picker, but these what-if triggers are full-width card titles — an overlaying popup covers the card header.
- A full-width trigger currently gets a content-width popup, which reads as visually disconnected from its trigger.

Changes:
- Widen `SelectContent`'s props to `SelectPrimitive.Popup.Props & Pick<SelectPrimitive.Positioner.Props, "side" | "sideOffset" | "align" | "alignItemWithTrigger">`, destructuring those four and passing them to `SelectPositioner` (with `sideOffset` keeping its current default of `4`). This mirrors the established precedent in `web/components/ui/tooltip.tsx`, which already does exactly this `Pick<>` for its own positioner props.
- Add `min-w-[var(--anchor-width)]` to the popup's classes. base-ui exposes `--anchor-width` on the positioner (`SelectPositionerCssVars`), so this sizes the popup to its trigger without hardcoding a width.

Both changes are additive: the existing week-picker call site passes none of the new props and keeps its current, already-verified behavior.

## 2. New shared `TeamSelect` component

**File:** `web/components/whatif/team-select.tsx` (create)

The three call sites would otherwise triplicate the same item mapping, the same id→name label resolution, and the same null guard. One component owns all three:

```tsx
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
})
```

(No explicit return-type annotation — matching every other component in this codebase, which lets TypeScript infer it.)

Responsibilities:
- Renders `Select` / `SelectTrigger` / `SelectValue` / `SelectContent` / `SelectItem`, one item per team, `value={team.team_id}`, text `{team.team_name}`.
- Resolves the trigger's display text from the current value via `SelectValue`'s children-function form: look up `teams.find((t) => t.team_id === v)?.team_name`, falling back to an empty string if no team matches.
- Guards the null arm of `onValueChange` (base-ui types single-select's callback as `Value | null`) so `onChange` only ever fires with a real `number` — no unsafe cast.
- Passes `alignItemWithTrigger={false}` on `SelectContent` so the popup drops *below* the trigger rather than covering the card title.
- Applies `label` as the trigger's `aria-label`, and merges `className` onto the trigger (via `cn`) so call sites control width.

Named `TeamSelect`, deliberately **not** `TeamPicker`: `team-picker.tsx` files already exist in `analyst/`, `beat-my-league/`, `lineup/`, and `waivers/`, and those are URL-driven server-navigated link lists — a different interaction entirely. Reusing that name would blur the distinction.

Lives in `web/components/whatif/` rather than `web/components/ui/` because it encodes this feature's domain (a `TeamRoster[]` shape), not a generic primitive.

## 3. Migrate the three call sites

**Files:** `web/components/whatif/roster-swap-builder.tsx`, `web/components/whatif/trade-builder.tsx` (modify)

Each raw `<select>`/`<option>` block is replaced by a `<TeamSelect>`. Preserved exactly:

- The `aria-label` strings, verbatim: `"Team to swap"`, `"Team sending players (side A)"`, `"Team sending players (side B)"`.
- The existing `selectTeam` / `selectTeamA` / `selectTeamB` handler functions and everything they do (clearing player selections, resetting `state` to `{ status: "idle" }`) — these are passed as `onChange` unchanged, not reimplemented.
- Visual weight: `font-semibold` on all three, `w-full` on all three, plus `max-w-xs` on the roster-swap one (matching its current classes).
- Placement inside `CardTitle`, unchanged.

No change to any surrounding logic: the `runSwap` / `runTrade` request builders, the `countsMatch` / `sameTeam` validation, the `summary` memo, and every rendered result component are untouched.

## 4. Null-guard the existing week picker

**File:** `web/components/dashboard/week-matchups-card.tsx` (modify)

Replace `onValueChange={(value) => setSelectedWeek(value as number)}` with a null-guarded handler (`if (value != null) setSelectedWeek(value)`), which also removes the cast. This clears a Minor finding deferred from the Phase 15 review and means all four `Select` consumers in the codebase handle the null arm identically.

## Non-goals

- The link-based `team-picker.tsx` components in `analyst/`, `beat-my-league/`, `lineup/`, and `waivers/` stay as they are. They are server-navigated URL links by design, not dropdowns, and converting them would change page-navigation behavior.
- No changes to what-if simulation logic, request shapes, API routes, or result rendering. This swaps an input control and nothing else.
- No new global CSS tokens; reuses the existing dark-glass theme via the already-styled `Select` primitive.

## Accessibility note

Native `<select>` works without JavaScript; base-ui's `Select` does not. That costs nothing here: all three call sites live inside `"use client"` components whose entire purpose is issuing live client-side simulation requests, so they are already JS-dependent. (This is the opposite of the dashboard's roster disclosure, which deliberately stayed a native `<details>` precisely because it does work without JS.)

The migration must preserve keyboard operability: tab to trigger, open with Enter/Space, arrow-key navigation, Enter to select, Escape to dismiss — base-ui provides this, but it is a verification item, not an assumption.

## Verification

Same standard as prior phases (`/web` has no unit-test harness in this repo):

- `cd web && npx tsc --noEmit`, `npx eslint .`, `npm run build` — all clean.
- Live browser check against the real fixture league (sim API via `uvicorn sim.api.app:app --host 127.0.0.1 --port 8123` from the repo root, plus the Next dev server), at `/league/885686492/whatif`:
  - Both what-if tabs render; all three dropdowns open, list every team, and show the current team's name on the trigger.
  - The popup opens *below* its trigger (not overlaying the card title) and matches the trigger's width.
  - Changing a team actually resets that side's player selections and clears any prior result — the behavior the old `onChange` had.
  - A full trade and a full roster swap still run end-to-end and render real before/after numbers.
  - Keyboard operation works as listed in the accessibility note above.
  - No console errors; no page-level horizontal scroll at 375px.
- Confirm the week picker on the dashboard still works after its null-guard change.
- `grep -rn "<select" web/components web/app` returns nothing.
