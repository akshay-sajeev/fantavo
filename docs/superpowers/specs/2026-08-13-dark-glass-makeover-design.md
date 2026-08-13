# Dark Glass Makeover — Design Spec

**Date:** 2026-08-13
**Status:** Approved, ready for implementation plan
**Scope:** `/web` only — Tailwind, shadcn primitives, page shell, dashboard page. No changes to `/sim`, `/ingest`, `/db`, or any simulation/analytics logic.

## Problem

The current UI follows `design-system/MASTER.md`, a reviewed-and-approved design system from Phase 5a/5b of this project: light-mode only, "Predictive Analytics" style, motion dialed to 3/10 ("Subtle"), and an anti-pattern list that explicitly forbids "Ornate design." The project owner wants a radical departure from that: a dark, glassmorphic/neumorphic, glowing, heavily animated athletic-dashboard aesthetic, referencing two mockups (a soccer club management dashboard and a live-sports betting dashboard).

This is a deliberate reversal of an approved decision, not an oversight, so it's being handled the way this project handles every other design decision: written up, approved, and logged in `docs/decisions.md`, with `design-system/MASTER.md` updated in place rather than left contradicting the shipped UI.

## Non-goals

- No change to `/sim`, `/ingest`, or any analytics/simulation logic. Purely visual and motion.
- No new chart types invented to match reference imagery that doesn't fit this app's data (e.g. no radar/spider chart — nothing in the current API responses maps to the categorical stats a soccer club app's radar chart shows).
- No light/dark toggle. Dark is the only theme going forward, matching the project owner's direction (Option A: supersede, not Option B: alternate theme).
- Full rollout to all 11 pages is out of scope for this spec. This spec covers the shared foundation (tokens, primitives, shell/nav) and one full page (the dashboard) as the reference implementation. Remaining pages (`power-rankings`, `risk`, `whatif`, `draft`, `playoffs`, `lineup-optimizer`, `waivers`, `beat-my-league`, `roast`, `analyst`) are fast-follow work once this lands and is approved, each reusing the same retthemed primitives.

## Approach

Push the visual change down into the design tokens and the shared `components/ui/*` primitives first, so pages that already compose `Card`, `Button`, `Badge`, `Table`, `Tabs`, `Tooltip`, and the shadcn `Chart` wrapper inherit most of the new look without per-page rewrites. Page-level work (this spec's dashboard, and later phases' remaining pages) then layers on structural/motion changes specific to that page's content.

## 1. Design tokens (`web/app/globals.css`, `design-system/MASTER.md`)

Replace the light palette in `:root` with a dark-only palette. No `.dark` class variant needed — dark is the only mode, so these become the base values directly (matching how the current file has no dark-mode block at all).

| Role | Token | Value | Notes |
|---|---|---|---|
| Background | `--background` | `#090D16` | obsidian base |
| Card surface | `--card` | `#0F1524` | used with `backdrop-blur-md` + ~70% opacity at the component level for the glass effect |
| Foreground | `--foreground` | `#E7ECF6` | primary text, verify ≥4.5:1 on `--background` and `--card` |
| Muted foreground | `--muted-foreground` | `#8B93A7` | secondary text, verify ≥4.5:1 |
| Border | `--border` / `--input` | `#1E293B` | used at reduced opacity (`/70`) on glass surfaces |
| Primary | `--primary` | `#3B82F6` | electric blue; glow variant `#60A5FA` for shadows/rings |
| Secondary | `--secondary` / `--muted` | dark neutral, distinct from `--card` | structural shadcn slot, not a data color |
| Brand accent | `--brand-accent` | `#39FF14` | neon green — rare use only (hero glow numbers, top-tier badges), not a general UI color, per Image 2's restrained use |
| Brand accent foreground | `--brand-accent-foreground` | dark (`#111827` or similar) | verify contrast on `#39FF14` per WCAG, same approach MASTER.md already used for the light-theme amber |
| Destructive | `--destructive` | `#F87171` | readable red-on-dark, verify contrast |
| Ring | `--ring` | `#60A5FA` | focus ring, must stay visible per MASTER's a11y anti-patterns |
| Chart 1–5 | `--chart-1..5` | `#3B82F6`, `#A78BFA`, `#39FF14`, `#2DD4BF`, `#F472B6` | glow-capable categorical set |

New tokens, additive:
- `--shadow-glow-primary`, `--shadow-glow-accent` — soft colored `box-shadow` values for hover/active glow states.
- `--radius-*` — unchanged from current `@theme inline` derivation.

Fonts unchanged: Fira Code (heading) / Fira Sans (body), loaded the same way via `next/font/google` in `app/layout.tsx`.

Motion dial in `MASTER.md` changes from 3/10 ("Subtle") to ~7/10. The "Ornate design" line is removed from MASTER's anti-patterns table; every other anti-pattern (no emoji icons, `cursor-pointer` on clickables, 4.5:1 contrast minimum, visible focus states, `prefers-reduced-motion` respected, no layout-shifting hover) is kept as-is — those are independent of light/dark and still apply. `globals.css` already has a `prefers-reduced-motion` block; the new Framer Motion primitives (below) must respect it too (Framer Motion's `useReducedMotion` hook, applied in the shared wrapper so individual call sites don't have to remember it).

## 2. Shell & navigation

- `components/shared/league-nav.tsx`: rebuilt from a horizontal scrolling tab bar into a fixed-left vertical icon rail. Each of the 11 pages gets one `lucide-react` icon (already a dependency) with an accessible label (visually a tooltip on hover/focus, not visible text at rest, collapsed rail) and a soft glowing pill background (`--shadow-glow-primary`) on the active item — the "neumorphic bubble" behavior. Keep `aria-current="page"` and keyboard focus behavior from the current implementation; this is a visual/structural change to the same nav semantics, not a rewrite of its logic.
- `app/league/[leagueId]/layout.tsx`: goes from a stacked column (`flex-col`) to a sidebar + content row (`flex-row`), sidebar fixed width, content area scrollable independently.
- `app/layout.tsx`: header shrinks to a slim bar with just the Fantavo wordmark/logo. No search box, notification bell, or avatar — those appear in the reference images but have no backing functionality in this app, and inventing non-functional UI chrome is out of scope.

## 3. Primitive retheme (`web/components/ui/*`)

Each of these keeps its existing props/API — only internal Tailwind classes and CSS variable usage change, so no consuming component needs to change its usage:

- **`Card`**: translucent glass (`bg-card/70 backdrop-blur-md`), soft `ring-1 ring-border/70`, hover state = slight lift (`-translate-y-0.5`) + glow ring, transform/shadow only (no layout shift, per MASTER's carried-over anti-pattern).
- **`Button`**: `default` variant becomes glow-blue; a new rare accent-style treatment (neon green, `brand-accent` token) reserved for standout CTAs, not the default button look.
- **`Badge`**: same variant set, dark-glass background per variant color.
- **`Table`**: dark rows, subtle glow-tinted row hover (matches Image 1's "Results" panel row highlighting).
- **`Tabs`**, **`Tooltip`**, **`Separator`**, **`Skeleton`**: token-level retheme only, no structural change.
- **`Chart`** (shadcn wrapper) + **`lib/chart-colors.ts`**: dark tooltip styling, chart series colors switched to the new `--chart-1..5` glow palette, line/bar strokes get a subtle SVG glow filter (`feGaussianBlur`-based, defined once and reused) — the only genuinely new visual technique introduced here, since no radar chart is being added.

## 4. Motion system (`web/components/ui/motion.tsx`, new file)

Three small Framer Motion primitives, each respecting `prefers-reduced-motion` internally so callers don't have to:

- **`<Reveal>`** — stagger wrapper for card grids: fade + 12px slide, ~300ms per child, `staggerChildren` on the parent. Used to wrap the groups of cards on a page (e.g. the dashboard's two-column grids).
- **`<TiltCard>`** — subtle mouse-tracked tilt (±3–4°, CSS `transform` only, capped range) plus a glow-ring hover shadow. Opt-in wrapper around `Card`, used for cards meant to feel interactive/featured (e.g. the dashboard's current-matchup hero card), not applied blanket to every card on the page.
- **`<AnimatedMeter>`** — animates a percentage from 0 to its real value on mount/viewport-enter, rendered as a small glowing horizontal meter. Used anywhere a win/playoff/title probability currently renders as a bare number, per the "percentages must glow" direction — never replaces the underlying number (the number stays, the meter is additive), and never rounds/derives a new percentage — it animates the exact value the API already returned.

Framer Motion is added as a new dependency (`web/package.json`); nothing else in the dependency tree changes.

## 5. Dashboard page (`web/app/league/[leagueId]/page.tsx` and its components)

Reference implementation for the new look, applied to the existing structure — no new sections, no new data fetched:

- **`StandingsTable`**: rows wrapped in `<Reveal>` for staggered entrance; the "Playoff %" and "Title %" columns render via `<AnimatedMeter>` instead of bare `formatPercent` text (the existing `formatPercent`-derived value is what's animated — no new computation). The top row (highest simulated title odds — data already sorted this way) gets a subtle accent-glow treatment, since it's reflecting existing sort order, not fabricating a "featured" pick.
- **`CurrentMatchupCard`**: promoted to a `<TiltCard>`-wrapped hero glass card — the closest analog to Image 1's featured player card this app has, given there are no player headshots/imagery in the data model. Existing logic (upcoming week vs. season-complete state) is untouched.
- **`RostersGrid`**: glass card + glow-tinted hover on the existing `<details>` rows. No structural change to the disclosure pattern (keeps its no-JS accessibility property).
- **`RemainingScheduleTable`**: retheme only.

## 6. Documentation

- `design-system/MASTER.md`: update the Color Palette, Design Dials (motion 3/10 → ~7/10), Component Specs (buttons/cards/inputs get the glass/glow values), and Anti-Patterns table (remove "Ornate design") in place. Add a short note at the top of the file (matching its existing convention of inline editorial notes) marking this as the phase that superseded the original Phase 5a palette, with a pointer to the `docs/decisions.md` entry.
- `docs/decisions.md`: new phase entry documenting this change — what was superseded, why (project owner's explicit direction, recorded here), and what's in/out of scope, matching the level of detail every prior phase entry uses.

## Testing / verification

This is a visual/motion change with no analytics logic touched, so:
- `make typecheck` (tsc, since no Python changes) and `make lint` (eslint) must stay clean.
- No new unit tests are needed for the motion primitives themselves (no business logic — verified by browser inspection instead), but any existing `vitest` tests that assert on now-changed class names or DOM structure (if any exist for `StandingsTable`, `CurrentMatchupCard`, `RostersGrid`, `league-nav.tsx`) must still pass or be updated to match the new structure, not deleted.
- Manual verification in a real browser (dev server) at desktop and 375px mobile widths: contrast check on the new palette (4.5:1 text minimum, carried over from MASTER's checklist), keyboard focus visibility on the new sidebar rail, `prefers-reduced-motion` actually disabling the new animations, no horizontal scroll introduced by the sidebar layout change.

## Open follow-ups (explicitly not in this spec)

- Rolling the retheme out to the remaining 10 pages, once this foundation is approved in a running app.
- Any future feature that would give a radar/spider-chart-shaped dataset (e.g. a per-position scoring breakdown) — worth revisiting the chart-type question then, not invented now.
