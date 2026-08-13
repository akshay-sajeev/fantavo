# Dark Glass Makeover (Foundation + Dashboard) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `/web`'s light "Predictive Analytics" theme with a dark, glassmorphic/neumorphic, glowing, motion-driven theme — implemented at the design-token and shared-primitive level plus one full reference page (the league dashboard) — per `docs/superpowers/specs/2026-08-13-dark-glass-makeover-design.md`.

**Architecture:** Push the visual change into `web/app/globals.css` CSS variables and the shared `web/components/ui/*` primitives first, so every page that already composes `Card`/`Button`/`Badge`/`Table`/`Tabs`/`Tooltip` inherits the new look for free. Layer new Framer Motion primitives (`Reveal`, `RevealItem`, `TiltCard`, `AnimatedMeter`) on top for entrance stagger, hover tilt/glow, and animated meters, applied first to the dashboard as the reference implementation. Structural nav changes (icon sidebar) live in `league-nav.tsx` and `league/[leagueId]/layout.tsx`.

**Tech Stack:** Next.js 15 (App Router, Server + Client Components), TypeScript, Tailwind CSS v4, shadcn (`@base-ui/react` primitives), Recharts, **Framer Motion (new dependency)**, `lucide-react`.

## Global Constraints

- Dark theme only — no light/dark toggle, no `.dark` class variant. Dark values go directly into `:root`.
- No changes to `/sim`, `/ingest`, `/db`, or any simulation/analytics logic — this is `/web`-only, visual and motion.
- No new chart types invented (no radar/spider chart) — nothing in this app's API responses maps to one yet.
- Fonts stay Fira Code (heading) / Fira Sans (body) — unchanged from current `app/layout.tsx`.
- Brand accent color is neon green `#39FF14` (not yellow — corrected from the spec's first draft), used rarely (hero glow numbers, top-tier badges, one Button variant), never as a general UI color.
- Framer Motion is the only new dependency this plan adds. Nothing else in `web/package.json`'s dependency tree changes.
- Every new/changed color combination must hold ≥4.5:1 contrast for text (WCAG AA). This plan's token choices were pre-computed against that bar (see Task 2) — don't substitute different hex values without re-checking.
- Carry over from `design-system/MASTER.md`'s existing anti-patterns (all independent of light/dark): SVG icons only (`lucide-react`, no emoji), `cursor-pointer` on all clickables, visible focus states, `prefers-reduced-motion` respected, no layout-shifting hover (transform/shadow only).
- Scope is the shared foundation (tokens, primitives, sidebar/shell) plus the dashboard page (`web/app/league/[leagueId]/page.tsx` and its four components) only. Do not touch any of the other 10 pages (`power-rankings`, `risk`, `whatif`, `draft`, `playoffs`, `lineup-optimizer`, `waivers`, `beat-my-league`, `roast`, `analyst`) — they inherit the token/primitive changes automatically and get their own page-specific pass in a later phase.
- **Verification commands for every task**: `/web` currently has no Makefile target and no vitest/unit-test harness wired up (despite `CLAUDE.md`'s general `make test` mention — that's not actually configured for `/web` in this repo today, and fixing that is out of scope here). Every prior phase in `docs/decisions.md` verified `/web` changes with `cd web && npx tsc --noEmit`, `cd web && npx eslint .`, and `cd web && npm run build`, plus a real-browser check. Use those same three commands after every task in this plan, and do a browser check (via the dev server) for any task that changes visible output.

---

## File Structure

New:
- `web/components/ui/motion.tsx` — `Reveal`, `RevealItem`, `revealContainerVariants`, `revealItemVariants`, `TiltCard`, `AnimatedMeter`. The one new shared file this plan adds.

Modified:
- `web/package.json` — add `framer-motion` dependency.
- `web/app/globals.css` — dark token palette + new glow-shadow variables, replacing the light palette.
- `web/lib/chart-colors.ts` — new glow-friendly `TEAM_CHART_COLORS` palette (unused by the dashboard itself, but a shared foundation file later pages depend on).
- `web/components/ui/card.tsx` — glass surface (`bg-card/70 backdrop-blur-md`) + hover lift/glow.
- `web/components/ui/button.tsx` — glow-blue `default` variant, new `accent` (neon green) variant.
- `web/components/ui/table.tsx` — glow-tinted row hover on `TableRow`.
- `web/components/shared/league-nav.tsx` — rebuilt from a horizontal tab bar into a fixed-left vertical icon rail.
- `web/app/league/[leagueId]/layout.tsx` — stacked column → sidebar + content row.
- `web/app/league/[leagueId]/page.tsx` — wraps the four dashboard cards in `Reveal`/`RevealItem` for staggered entrance.
- `web/components/dashboard/standings-table.tsx` — becomes a Client Component; per-row stagger via `motion.tbody`/`motion.tr`, `AnimatedMeter` on the Playoff %/Title % columns, subtle glow on the top-ranked row.
- `web/components/dashboard/current-matchup-card.tsx` — becomes a Client Component; wrapped in `TiltCard` as the page's hero glass card.
- `web/components/dashboard/rosters-grid.tsx` — glow-tinted hover on the existing `<details>` rows (CSS only, stays a Server Component).
- `design-system/MASTER.md` — palette, design dials, component specs, and anti-patterns table updated in place.
- `docs/decisions.md` — new Phase 14 entry.

Not modified (inherit the new look automatically via CSS variables, per the spec's own analysis — confirmed by reading each file before deciding this): `web/components/ui/badge.tsx`, `web/components/ui/tabs.tsx`, `web/components/ui/tooltip.tsx`, `web/components/ui/separator.tsx`, `web/components/ui/skeleton.tsx`, `web/app/layout.tsx` (already `bg-card/95 backdrop-blur`, already just the wordmark — nothing to change), `web/components/dashboard/remaining-schedule-table.tsx` (inherits `Card`'s new glass treatment with no direct edit needed). `web/components/ui/chart.tsx` and its SVG glow-filter technique are explicitly **deferred** to the phase that retthemes the first chart-consuming page (`power-rankings`) — building it now would mean shipping code with zero consumers in this plan's scope and no way to visually verify it, which this plan's verification standard (real browser check) can't satisfy honestly.

---

### Task 1: Add Framer Motion dependency

**Files:**
- Modify: `web/package.json`

**Interfaces:**
- Produces: `framer-motion` importable from any Client Component in `/web` (used starting Task 7).

- [ ] **Step 1: Add the dependency**

In `web/package.json`, add to `"dependencies"` (alphabetical, matching the existing list's ordering):

```json
    "framer-motion": "^13.1.0",
```

Full resulting `"dependencies"` block:

```json
  "dependencies": {
    "@base-ui/react": "^1.7.0",
    "class-variance-authority": "^0.7.1",
    "clsx": "^2.1.1",
    "framer-motion": "^13.1.0",
    "html-to-image": "^1.11.13",
    "lucide-react": "^1.31.0",
    "next": "^15.5.23",
    "react": "19.2.8",
    "react-dom": "19.2.8",
    "recharts": "^3.8.0",
    "server-only": "^0.0.1",
    "shadcn": "^4.17.0",
    "tailwind-merge": "^3.6.0",
    "tw-animate-css": "^1.4.0"
  },
```

- [ ] **Step 2: Install**

Run: `cd web && npm install`
Expected: lockfile updates, `framer-motion` appears under `web/node_modules/framer-motion`.

- [ ] **Step 3: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint .`
Expected: both clean (no code references it yet, so this just confirms the install didn't break anything).

- [ ] **Step 4: Commit**

```bash
cd web && git add package.json package-lock.json
git commit -m "Web: add framer-motion dependency"
```

---

### Task 2: Dark design tokens

**Files:**
- Modify: `web/app/globals.css`

**Interfaces:**
- Produces: every `--color-*` CSS variable Tailwind's `@theme inline` block already maps (`background`, `foreground`, `card`, `primary`, `brand-accent`, `chart-1..5`, etc. — unchanged names, new values), plus four new plain CSS variables: `--shadow-glow-primary`, `--shadow-glow-primary-lg`, `--shadow-glow-accent`, `--shadow-glow-accent-lg`, referenced by later tasks via Tailwind arbitrary values like `shadow-[var(--shadow-glow-primary)]`.

- [ ] **Step 1: Replace the `:root` block and its preceding comment**

Open `web/app/globals.css`. Replace everything from the `/* Fantavo palette, ...` comment through the closing `}` of `:root { ... }` (currently lines 61–120) with:

```css
/* Fantavo palette -- Dark Glass Makeover (2026-08-13), superseding the
   original Phase 5a/5b light palette. See
   docs/superpowers/specs/2026-08-13-dark-glass-makeover-design.md for the
   approved design and docs/decisions.md's Phase 14 entry for why. Dark-only:
   no light/dark toggle, matching the project owner's explicit direction to
   replace rather than add a theme switch. Every text/background pairing
   below was checked against WCAG 4.5:1 for normal text before being chosen
   (see the spec's contrast notes); don't swap in a different hex without
   re-checking. */
:root {
  --background: #090d16;
  --foreground: #e7ecf6;
  --card: #0f1524;
  --card-foreground: #e7ecf6;
  --popover: #0f1524;
  --popover-foreground: #e7ecf6;
  --primary: #2563eb;
  --primary-foreground: #f8fafc;
  /* Structural shadcn "secondary" (generic filled surface / muted
     background) -- a step lighter than --background but distinct from
     --card's glass tone, so ordinary secondary buttons/surfaces don't
     compete visually with real chart data or the primary glow-blue CTA. */
  --secondary: #141b2e;
  --secondary-foreground: #e7ecf6;
  --muted: #141b2e;
  --muted-foreground: #8b93a7;
  /* Structural shadcn "accent" (hover/selected tint across menus, tabs,
     table rows) -- a low-key dark tint, not the brand accent green. See
     --brand-accent below for the real "Accent/CTA" color, reserved for
     rare, high-signal use. */
  --accent: #16203a;
  --accent-foreground: #e7ecf6;
  --brand-accent: #39ff14;
  /* #39FF14 measures ~15.5:1 against dark ink and ~1.4:1 against white, so
     brand-accent-colored badges/buttons must always use dark text, never
     white -- same asymmetric-contrast situation MASTER.md's original amber
     was in, just flipped (there, white text failed; here, white text fails
     even harder and dark text is the only option). */
  --brand-accent-foreground: #0b1220;
  --data-blue: #3b82f6;
  --destructive: #f87171;
  --destructive-foreground: #0b1220;
  --border: #1e293b;
  --input: #1e293b;
  --ring: #60a5fa;
  --chart-1: #3b82f6;
  --chart-2: #a78bfa;
  --chart-3: #39ff14;
  --chart-4: #2dd4bf;
  --chart-5: #f472b6;
  --radius: 0.5rem;
  --sidebar: #0f1524;
  --sidebar-foreground: #e7ecf6;
  --sidebar-primary: #2563eb;
  --sidebar-primary-foreground: #f8fafc;
  --sidebar-accent: #16203a;
  --sidebar-accent-foreground: #e7ecf6;
  --sidebar-border: #1e293b;
  --sidebar-ring: #60a5fa;

  /* Glow shadows -- soft colored box-shadows for hover/active/featured
     states, referenced via Tailwind arbitrary values (e.g.
     shadow-[var(--shadow-glow-primary)]). Plain CSS variables, not part of
     the @theme inline color mapping below, since these are shadow values
     rather than colors. */
  --shadow-glow-primary: 0 0 16px 0 rgba(59, 130, 246, 0.35);
  --shadow-glow-primary-lg: 0 0 28px 2px rgba(59, 130, 246, 0.5);
  --shadow-glow-accent: 0 0 16px 0 rgba(57, 255, 20, 0.3);
  --shadow-glow-accent-lg: 0 0 28px 2px rgba(57, 255, 20, 0.45);
}
```

- [ ] **Step 2: Update the `@theme inline` block's brand-accent comment**

Find (near the top of the file, inside `@theme inline { ... }`):

```css
  /* MASTER.md's "Accent/CTA" amber, kept as its own token rather than
     overloading shadcn's structural --accent (which drives every generic
     hover/selected state across shadcn components). Used explicitly and
     sparingly: CTA-style highlights only, e.g. the current-week badge and
     the top power-ranking row. See docs/decisions.md Phase 5b. */
  --color-brand-accent: var(--brand-accent);
```

Replace with:

```css
  /* MASTER.md's "Accent/CTA" color -- amber in the original Phase 5a/5b
     light palette, changed to neon green in the Dark Glass Makeover (see
     docs/decisions.md Phase 5b and Phase 14). Kept as its own token rather
     than overloading shadcn's structural --accent (which drives every
     generic hover/selected state across shadcn components). Used explicitly
     and sparingly: CTA-style highlights only, e.g. the current-week badge
     and the top power-ranking row. */
  --color-brand-accent: var(--brand-accent);
```

- [ ] **Step 3: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint .`
Expected: both clean (CSS changes don't affect TS/lint, this just confirms nothing else broke).

Run: `cd web && npm run build`
Expected: production build succeeds.

Then start the dev server and check visually:
- Load any page (e.g. `/league/<id>`). Background should be obsidian (`#090D16`), text light and readable, existing cards still visible (not yet glass — that's Task 4).
- Open devtools and confirm computed `color` on body text and `background-color` on `body` match the new hex values.

- [ ] **Step 4: Commit**

```bash
git add web/app/globals.css
git commit -m "Web: dark design tokens for the Dark Glass Makeover"
```

---

### Task 3: Chart color palette

**Files:**
- Modify: `web/lib/chart-colors.ts`

**Interfaces:**
- Produces: `TEAM_CHART_COLORS: readonly string[]` (same shape/length as before, new values), `teamColor(index: number): string` (unchanged signature).

- [ ] **Step 1: Replace the palette**

Replace the full file content with:

```ts
/**
 * Categorical palette for per-team chart series (power rankings bars,
 * finish-distribution strips). design-system/MASTER.md specifies role
 * colors (primary blue, brand accent green, destructive red) but not a full
 * categorical sweep for an arbitrary N-team league, so this extends those
 * hues into a larger, distinct-per-team sequence, chosen to read clearly on
 * the Dark Glass Makeover's obsidian background -- documented in
 * docs/decisions.md Phase 14 (originally Phase 5b for the light-theme
 * version of this file). Comfortably covers this app's league sizes (10-12
 * teams per CLAUDE.md's chart-recommendation note).
 */
export const TEAM_CHART_COLORS: readonly string[] = [
  "#3B82F6", // primary glow blue
  "#39FF14", // brand accent neon green
  "#A78BFA", // violet
  "#2DD4BF", // teal
  "#F472B6", // pink
  "#F87171", // destructive red
  "#FACC15", // gold
  "#60A5FA", // light blue
  "#C084FC", // light violet
  "#34D399", // green
  "#38BDF8", // cyan
  "#FB923C", // orange
];

export function teamColor(index: number): string {
  return TEAM_CHART_COLORS[index % TEAM_CHART_COLORS.length];
}
```

- [ ] **Step 2: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint .`
Expected: both clean.

- [ ] **Step 3: Commit**

```bash
git add web/lib/chart-colors.ts
git commit -m "Web: recolor team chart palette for dark theme"
```

---

### Task 4: Card primitive — glass surface

**Files:**
- Modify: `web/components/ui/card.tsx`

**Interfaces:**
- Consumes: `--shadow-glow-primary` (Task 2).
- Produces: `Card` keeps its exact existing props/API (`size?: "default" | "sm"`, all standard `div` props) — no consumer needs to change.

- [ ] **Step 1: Edit the `Card` className**

In `web/components/ui/card.tsx`, find:

```tsx
        "group/card flex flex-col gap-(--card-spacing) overflow-hidden rounded-xl bg-card py-(--card-spacing) text-sm text-card-foreground ring-1 ring-foreground/10 [--card-spacing:--spacing(4)] has-data-[slot=card-footer]:pb-0 has-[>img:first-child]:pt-0 data-[size=sm]:[--card-spacing:--spacing(3)] data-[size=sm]:has-data-[slot=card-footer]:pb-0 *:[img:first-child]:rounded-t-xl *:[img:last-child]:rounded-b-xl",
```

Replace with:

```tsx
        "group/card flex flex-col gap-(--card-spacing) overflow-hidden rounded-xl bg-card/70 py-(--card-spacing) text-sm text-card-foreground ring-1 ring-border/70 backdrop-blur-md transition-all duration-200 hover:-translate-y-0.5 hover:shadow-[var(--shadow-glow-primary)] [--card-spacing:--spacing(4)] has-data-[slot=card-footer]:pb-0 has-[>img:first-child]:pt-0 data-[size=sm]:[--card-spacing:--spacing(3)] data-[size=sm]:has-data-[slot=card-footer]:pb-0 *:[img:first-child]:rounded-t-xl *:[img:last-child]:rounded-b-xl",
```

- [ ] **Step 2: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build`
Expected: all clean.

Start the dev server, load `/league/<id>`: cards should now look translucent/blurred over the obsidian background, and lift slightly with a soft blue glow on hover (no layout shift — check the card's neighbors don't reflow when you hover).

- [ ] **Step 3: Commit**

```bash
git add web/components/ui/card.tsx
git commit -m "Web: glass surface + hover glow on the Card primitive"
```

---

### Task 5: Button primitive — glow + accent variant

**Files:**
- Modify: `web/components/ui/button.tsx`

**Interfaces:**
- Consumes: `--shadow-glow-primary`, `--shadow-glow-primary-lg`, `--shadow-glow-accent`, `--shadow-glow-accent-lg` (Task 2), `--brand-accent`/`--brand-accent-foreground` (Task 2).
- Produces: `buttonVariants` gains a new `"accent"` value for its `variant` prop, in addition to the existing `"default" | "outline" | "secondary" | "ghost" | "destructive" | "link"`. Existing variants keep their names — no consumer changes required unless they want to opt into `"accent"`.

- [ ] **Step 1: Edit the `variant` map**

In `web/components/ui/button.tsx`, find:

```tsx
      variants: {
        default: "bg-primary text-primary-foreground hover:bg-primary/80",
        outline:
```

Replace with:

```tsx
      variants: {
        default:
          "bg-primary text-primary-foreground shadow-[var(--shadow-glow-primary)] hover:bg-primary/90 hover:shadow-[var(--shadow-glow-primary-lg)]",
        accent:
          "bg-brand-accent text-brand-accent-foreground shadow-[var(--shadow-glow-accent)] hover:bg-brand-accent/90 hover:shadow-[var(--shadow-glow-accent-lg)]",
        outline:
```

(Everything after `outline:` — `outline`, `secondary`, `ghost`, `destructive`, `link` — stays exactly as-is.)

- [ ] **Step 2: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build`
Expected: all clean.

In the dev server, find any existing `<Button>` (default variant) in the app and confirm it now has a soft blue glow that intensifies on hover, with no layout shift.

- [ ] **Step 3: Commit**

```bash
git add web/components/ui/button.tsx
git commit -m "Web: glow-blue default Button + new neon-green accent variant"
```

---

### Task 6: Table primitive — glow row hover

**Files:**
- Modify: `web/components/ui/table.tsx`

**Interfaces:**
- Consumes: `--color-primary` (already mapped by `@theme inline` from `--primary`, Task 2).
- Produces: `TableRow` keeps its existing props/API.

- [ ] **Step 1: Edit `TableRow`'s className**

In `web/components/ui/table.tsx`, find:

```tsx
      className={cn(
        "border-b transition-colors hover:bg-muted/50 has-aria-expanded:bg-muted/50 data-[state=selected]:bg-muted",
        className
      )}
```

Replace with:

```tsx
      className={cn(
        "border-b border-border/70 transition-all duration-150 hover:bg-primary/5 hover:shadow-[inset_2px_0_0_0_var(--color-primary)] has-aria-expanded:bg-muted/50 data-[state=selected]:bg-muted",
        className
      )}
```

- [ ] **Step 2: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build`
Expected: all clean.

In the dev server, load any page with a `Table` (e.g. `/league/<id>`, once Task 11 lands, or any other page today) and confirm rows get a subtle blue-tinted background plus a thin glowing left edge on hover.

- [ ] **Step 3: Commit**

```bash
git add web/components/ui/table.tsx
git commit -m "Web: glow-tinted row hover on the Table primitive"
```

---

### Task 7: Motion primitives (`Reveal`, `RevealItem`, `TiltCard`, `AnimatedMeter`)

**Files:**
- Create: `web/components/ui/motion.tsx`

**Interfaces:**
- Consumes: `framer-motion` (Task 1), `cn` from `web/lib/utils.ts`, `--shadow-glow-primary-lg` / `--shadow-glow-primary` / `--shadow-glow-accent` (Task 2).
- Produces (all used by later tasks):
  - `revealContainerVariants: Variants`, `revealItemVariants: Variants` — reusable timing, for consumers that can't use the div wrappers directly (Task 11).
  - `Reveal({ children, className }): JSX.Element` — stagger container, renders a plain `div` (no animation) when `prefers-reduced-motion` is set.
  - `RevealItem({ children, className }): JSX.Element` — one staggered child.
  - `TiltCard({ children, className }): JSX.Element` — mouse-tracked tilt + hover glow wrapper, opt-in.
  - `AnimatedMeter({ value, label, glow }): JSX.Element` — `value: number` (0-1 fraction), `label: string` (accessible label), `glow?: "primary" | "accent"` (default `"primary"`).

- [ ] **Step 1: Write the file**

```tsx
"use client";

import { useRef } from "react";
import type { PointerEvent, ReactNode } from "react";
import {
  motion,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
  type Variants,
} from "framer-motion";
import { cn } from "@/lib/utils";

/**
 * Shared entrance-animation variants -- exported so pages that can't use the
 * <Reveal>/<RevealItem> div wrappers directly (e.g. StandingsTable, which
 * needs motion.tbody/motion.tr to stagger real table rows) still get the
 * exact same timing/easing rather than duplicating magic numbers.
 */
export const revealContainerVariants: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.08 } },
};

export const revealItemVariants: Variants = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.3, ease: "easeOut" } },
};

/**
 * Stagger container for a page-level grid of cards. Wrap the grid in
 * <Reveal>, wrap each direct card in <RevealItem>. Respects
 * prefers-reduced-motion by rendering a plain div with no animation.
 */
export function Reveal({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const reduceMotion = useReducedMotion();
  if (reduceMotion) {
    return <div className={className}>{children}</div>;
  }
  return (
    <motion.div
      className={className}
      variants={revealContainerVariants}
      initial="hidden"
      animate="show"
    >
      {children}
    </motion.div>
  );
}

export function RevealItem({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const reduceMotion = useReducedMotion();
  if (reduceMotion) {
    return <div className={className}>{children}</div>;
  }
  return (
    <motion.div className={className} variants={revealItemVariants}>
      {children}
    </motion.div>
  );
}

const MAX_TILT_DEG = 4;

/**
 * Opt-in wrapper for a single featured/interactive card: subtle
 * mouse-tracked tilt (transform-only, capped, no layout shift) plus a
 * glow-ring hover shadow. Not applied blanket to every card -- only ones
 * meant to feel interactive/featured (e.g. the dashboard's current-matchup
 * hero card).
 */
export function TiltCard({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const reduceMotion = useReducedMotion();
  const ref = useRef<HTMLDivElement>(null);
  const x = useMotionValue(0.5);
  const y = useMotionValue(0.5);
  const springX = useSpring(x, { stiffness: 200, damping: 20 });
  const springY = useSpring(y, { stiffness: 200, damping: 20 });
  const rotateX = useTransform(springY, [0, 1], [MAX_TILT_DEG, -MAX_TILT_DEG]);
  const rotateY = useTransform(springX, [0, 1], [-MAX_TILT_DEG, MAX_TILT_DEG]);

  function handlePointerMove(event: PointerEvent<HTMLDivElement>) {
    const bounds = ref.current?.getBoundingClientRect();
    if (!bounds) return;
    x.set((event.clientX - bounds.left) / bounds.width);
    y.set((event.clientY - bounds.top) / bounds.height);
  }

  function handlePointerLeave() {
    x.set(0.5);
    y.set(0.5);
  }

  if (reduceMotion) {
    return <div className={className}>{children}</div>;
  }

  return (
    <motion.div
      ref={ref}
      className={cn(
        "[perspective:1000px] rounded-xl transition-shadow duration-300 hover:shadow-[var(--shadow-glow-primary-lg)]",
        className
      )}
      onPointerMove={handlePointerMove}
      onPointerLeave={handlePointerLeave}
      style={{ rotateX, rotateY, transformStyle: "preserve-3d" }}
    >
      {children}
    </motion.div>
  );
}

/**
 * Animates a 0-1 fraction (already computed by the API, never derived here)
 * from 0 to its real value as a glowing horizontal meter. Additive only --
 * callers keep rendering the formatted percentage text alongside this; the
 * meter never replaces the number.
 */
export function AnimatedMeter({
  value,
  label,
  glow = "primary",
}: {
  value: number;
  label: string;
  glow?: "primary" | "accent";
}) {
  const reduceMotion = useReducedMotion();
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const glowShadow =
    glow === "accent" ? "var(--shadow-glow-accent)" : "var(--shadow-glow-primary)";
  const barColor = glow === "accent" ? "bg-brand-accent" : "bg-primary";

  return (
    <div
      className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-muted"
      role="meter"
      aria-label={label}
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <motion.div
        className={cn("h-full rounded-full", barColor)}
        style={{ boxShadow: glowShadow }}
        initial={reduceMotion ? false : { width: 0 }}
        animate={{ width: `${pct}%` }}
        transition={{ duration: reduceMotion ? 0 : 0.6, ease: "easeOut" }}
      />
    </div>
  );
}
```

- [ ] **Step 2: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build`
Expected: all clean. (Nothing imports this file yet, so this only confirms it compiles standalone.)

- [ ] **Step 3: Commit**

```bash
git add web/components/ui/motion.tsx
git commit -m "Web: add Reveal/RevealItem/TiltCard/AnimatedMeter motion primitives"
```

---

### Task 8: Sidebar icon rail

**Files:**
- Modify: `web/components/shared/league-nav.tsx`

**Interfaces:**
- Consumes: `Tooltip`/`TooltipTrigger`/`TooltipContent` (`web/components/ui/tooltip.tsx`, already wired to a `TooltipProvider` in `web/app/layout.tsx`), `lucide-react` icons, `--shadow-glow-primary` (Task 2).
- Produces: `LeagueNav({ leagueId }: { leagueId: number })` — same props as before, still a Client Component, still renders one link per page with the same 11 `href`s and the same `aria-current="page"` semantics. Consumed by Task 9's `layout.tsx`.

- [ ] **Step 1: Replace the file**

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  TrendingUp,
  ShieldAlert,
  GitCompare,
  ClipboardList,
  Trophy,
  ListChecks,
  UserPlus,
  Swords,
  Flame,
  Bot,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export function LeagueNav({ leagueId }: { leagueId: number }) {
  const pathname = usePathname();
  const base = `/league/${leagueId}`;
  const tabs: { href: string; label: string; icon: LucideIcon }[] = [
    { href: base, label: "Overview", icon: LayoutDashboard },
    { href: `${base}/power-rankings`, label: "Power Rankings", icon: TrendingUp },
    { href: `${base}/risk`, label: "Roster Risk", icon: ShieldAlert },
    { href: `${base}/whatif`, label: "What-If", icon: GitCompare },
    { href: `${base}/draft`, label: "Draft Autopsy", icon: ClipboardList },
    { href: `${base}/playoffs`, label: "Playoff Planner", icon: Trophy },
    { href: `${base}/lineup-optimizer`, label: "Lineup Optimizer", icon: ListChecks },
    { href: `${base}/waivers`, label: "Waiver Intelligence", icon: UserPlus },
    { href: `${base}/beat-my-league`, label: "Beat My League", icon: Swords },
    { href: `${base}/roast`, label: "Roast", icon: Flame },
    { href: `${base}/analyst`, label: "AI Analyst", icon: Bot },
  ];

  return (
    <nav
      aria-label="League sections"
      className="sticky top-14 flex h-[calc(100vh-3.5rem)] w-14 shrink-0 flex-col items-center gap-1 overflow-y-auto border-r border-border/70 bg-card/40 py-3 backdrop-blur-md"
    >
      {tabs.map((tab) => {
        const isActive = pathname === tab.href;
        const Icon = tab.icon;
        return (
          <Tooltip key={tab.href}>
            <TooltipTrigger
              render={
                <Link
                  href={tab.href}
                  aria-current={isActive ? "page" : undefined}
                  aria-label={tab.label}
                  className={cn(
                    "flex h-10 w-10 shrink-0 cursor-pointer items-center justify-center rounded-xl transition-all duration-150",
                    "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring",
                    isActive
                      ? "bg-primary/15 text-primary shadow-[var(--shadow-glow-primary)]"
                      : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                  )}
                />
              }
            >
              <Icon className="h-5 w-5" aria-hidden="true" />
            </TooltipTrigger>
            <TooltipContent side="right">{tab.label}</TooltipContent>
          </Tooltip>
        );
      })}
    </nav>
  );
}
```

- [ ] **Step 2: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build`
Expected: all clean.

(Full visual/keyboard verification happens in Task 9, once the layout actually renders this as a sidebar instead of a top bar.)

- [ ] **Step 3: Commit**

```bash
git add web/components/shared/league-nav.tsx
git commit -m "Web: rebuild league nav as a vertical icon rail with tooltips"
```

---

### Task 9: League layout — sidebar + content row

**Files:**
- Modify: `web/app/league/[leagueId]/layout.tsx`

**Interfaces:**
- Consumes: `LeagueNav` (Task 8).
- Produces: same exported default layout component signature (`{ children, params }`), same route behavior — every page under `/league/[leagueId]` renders unchanged except for the new shell around it.

- [ ] **Step 1: Edit the layout**

Replace the file's return statement. Current:

```tsx
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-4 px-4 py-4">
      <LeagueNav leagueId={Number(leagueId)} />
      <div className="flex-1">{children}</div>
    </div>
  );
```

Replace with:

```tsx
  return (
    <div className="flex w-full flex-1">
      <LeagueNav leagueId={Number(leagueId)} />
      <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col px-4 py-4">
        {children}
      </div>
    </div>
  );
```

(The rest of the file — the `import`, the function signature, the `params` destructuring — is unchanged.)

- [ ] **Step 2: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build`
Expected: all clean.

Start the dev server, load `/league/<id>` (any of the 11 pages), and check:
- Icon rail is fixed on the far left, full height below the header, scrolls independently if content is taller than the viewport.
- The active page's icon has the glow-pill background; hovering another icon shows its tooltip after ~150ms (the `TooltipProvider` delay already set in `app/layout.tsx`).
- Tab through the icons with keyboard only — focus ring is visible on each, `Enter` navigates.
- At 375px width (`resize_window` to mobile preset): confirm no page-level horizontal scroll (`document.documentElement.scrollWidth === document.documentElement.clientWidth`) and the sidebar doesn't crowd out content unreadably — narrow, icon-only rail should still fit at 375px since it's only `w-14` (56px).

- [ ] **Step 3: Commit**

```bash
git add "web/app/league/[leagueId]/layout.tsx"
git commit -m "Web: league layout becomes sidebar + content, not stacked column"
```

---

### Task 10: Dashboard page — staggered card entrance

**Files:**
- Modify: `web/app/league/[leagueId]/page.tsx`

**Interfaces:**
- Consumes: `Reveal`, `RevealItem` (Task 7).
- Produces: same exported default page component signature and data-fetching behavior — no change to what's fetched or how errors are handled, only how the four resulting cards are wrapped for entrance animation.

- [ ] **Step 1: Edit the page**

Replace the file's return statement. Current:

```tsx
  return (
    <div className="space-y-4 py-4">
      <div>
        <h1 className="font-heading text-2xl font-semibold tracking-tight">League Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          League {simulation.league_id} &middot; {simulation.season_id} season &middot; based on{" "}
          {simulation.n_sims.toLocaleString()} simulated seasons
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle className="font-heading text-base">Standings</CardTitle>
            </CardHeader>
            <CardContent>
              <StandingsTable simulation={simulation} schedule={schedule} />
            </CardContent>
          </Card>
        </div>
        <CurrentMatchupCard schedule={schedule} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <RostersGrid teams={roster.teams} />
        </div>
        <RemainingScheduleTable schedule={schedule} />
      </div>
    </div>
  );
```

Replace with:

```tsx
  return (
    <div className="space-y-4 py-4">
      <div>
        <h1 className="font-heading text-2xl font-semibold tracking-tight">League Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          League {simulation.league_id} &middot; {simulation.season_id} season &middot; based on{" "}
          {simulation.n_sims.toLocaleString()} simulated seasons
        </p>
      </div>

      <Reveal className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <RevealItem className="lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle className="font-heading text-base">Standings</CardTitle>
            </CardHeader>
            <CardContent>
              <StandingsTable simulation={simulation} schedule={schedule} />
            </CardContent>
          </Card>
        </RevealItem>
        <RevealItem>
          <CurrentMatchupCard schedule={schedule} />
        </RevealItem>
      </Reveal>

      <Reveal className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <RevealItem className="lg:col-span-2">
          <RostersGrid teams={roster.teams} />
        </RevealItem>
        <RevealItem>
          <RemainingScheduleTable schedule={schedule} />
        </RevealItem>
      </Reveal>
    </div>
  );
```

- [ ] **Step 2: Add the import**

At the top of the file, alongside the other imports:

```tsx
import { Reveal, RevealItem } from "@/components/ui/motion";
```

- [ ] **Step 3: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build`
Expected: all clean. (`page.tsx` stays a Server Component — it renders `Reveal`/`RevealItem` as Client Component children, which Next.js allows without the page itself needing `"use client"`.)

Load `/league/<id>` in the dev server: on a hard refresh, the four cards should fade in and slide up ~12px, staggered (Standings, then the matchup card; then Rosters, then Remaining Schedule — each `Reveal` group staggers its own two children). Enable "prefers reduced motion" in the OS/browser and reload — cards should appear instantly, no animation.

- [ ] **Step 4: Commit**

```bash
git add "web/app/league/[leagueId]/page.tsx"
git commit -m "Web: staggered entrance for the dashboard's card groups"
```

---

### Task 11: StandingsTable — animated meters + row stagger

**Files:**
- Modify: `web/components/dashboard/standings-table.tsx`

**Interfaces:**
- Consumes: `AnimatedMeter`, `revealContainerVariants`, `revealItemVariants` (Task 7), `motion`/`useReducedMotion` from `framer-motion` directly (for `motion.tbody`/`motion.tr`, which the `Reveal`/`RevealItem` div wrappers can't produce).
- Produces: same exported `StandingsTable({ simulation, schedule })` signature and the same sort/tally/divergence logic — this task changes rendering only, not the underlying computation, which was already presentational sorting of API-supplied values (not new analytics logic; `formatPercent`, `tallyActualRecords`, and the `mean_wins` sort all already existed).
- **Note:** this file becomes a Client Component (`"use client"`) because `motion.tbody`/`motion.tr` and `useReducedMotion` require it. It's still rendered from `page.tsx` (a Server Component) with plain serializable props (`simulation`, `schedule`), which Next.js supports natively — no architectural change beyond where this one component's JS executes.

- [ ] **Step 1: Replace the file**

```tsx
"use client";

import { AlertTriangle, Info } from "lucide-react";
import { motion, useReducedMotion } from "framer-motion";
import {
  Table,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { AnimatedMeter, revealContainerVariants, revealItemVariants } from "@/components/ui/motion";
import { formatPercent, formatWins } from "@/lib/format";
import { tallyActualRecords, totalGamesPlayed } from "@/lib/standings";
import { cn } from "@/lib/utils";
import type { ScheduleResponse, SimulationResponse } from "@/lib/types";

/**
 * "Lead with analysis, not a copy of ESPN's UI" + "where roster strength and
 * record disagree, say so" (PLAN.md). Simulated strength is ranked by
 * `mean_wins` from the simulation endpoint (never a frontend-computed
 * score). Actual record is tallied from the schedule endpoint's own
 * per-matchup `winner` field (see lib/standings.ts) -- literal counting of
 * already-decided results, not a projection.
 *
 * When zero games have been played league-wide (true for this pre-season /
 * synthetic league today), there is nothing to compare a projection
 * against, so this renders an explicit "no games played yet" note instead
 * of a fabricated disagreement -- never invents a game result. Once real
 * weekly results exist, the "Diverges from projection" flag activates
 * automatically, on the same code path, with no special-casing.
 */
export function StandingsTable({
  simulation,
  schedule,
}: {
  simulation: SimulationResponse;
  schedule: ScheduleResponse;
}) {
  const reduceMotion = useReducedMotion();
  const bySimStrength = [...simulation.teams].sort((a, b) => b.mean_wins - a.mean_wins);
  const actualRecords = tallyActualRecords(schedule);
  const gamesPlayed = totalGamesPlayed(actualRecords);

  const actualRank = new Map<number, number>();
  if (gamesPlayed > 0) {
    const byActualWins = [...bySimStrength].sort((a, b) => {
      const aw = actualRecords.get(a.team_id)?.wins ?? 0;
      const bw = actualRecords.get(b.team_id)?.wins ?? 0;
      return bw - aw;
    });
    byActualWins.forEach((t, i) => actualRank.set(t.team_id, i));
  }

  return (
    <div className="space-y-3">
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

      <div className="overflow-x-auto rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">#</TableHead>
              <TableHead>Team</TableHead>
              <TableHead className="text-right">
                {gamesPlayed > 0 ? "Actual record" : "Projected record"}
              </TableHead>
              <TableHead className="text-right">Playoff %</TableHead>
              <TableHead className="text-right">Title %</TableHead>
              {gamesPlayed > 0 && <TableHead className="text-right">Note</TableHead>}
            </TableRow>
          </TableHeader>
          <motion.tbody
            data-slot="table-body"
            className="[&_tr:last-child]:border-0"
            variants={reduceMotion ? undefined : revealContainerVariants}
            initial={reduceMotion ? undefined : "hidden"}
            animate={reduceMotion ? undefined : "show"}
          >
            {bySimStrength.map((team, projectedRank) => {
              const record = actualRecords.get(team.team_id);
              const diverges =
                gamesPlayed > 0 &&
                Math.abs((actualRank.get(team.team_id) ?? projectedRank) - projectedRank) >= 3;
              const isTop = projectedRank === 0;
              return (
                <motion.tr
                  key={team.team_id}
                  variants={reduceMotion ? undefined : revealItemVariants}
                  className={cn(
                    "border-b border-border/70 transition-all duration-150 hover:bg-primary/5 hover:shadow-[inset_2px_0_0_0_var(--color-primary)] data-[state=selected]:bg-muted",
                    isTop && "bg-brand-accent/5 shadow-[inset_2px_0_0_0_var(--color-brand-accent)]"
                  )}
                >
                  <TableCell className="tabular-nums text-muted-foreground">
                    {projectedRank + 1}
                  </TableCell>
                  <TableCell className="font-medium">{team.team_name}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {gamesPlayed > 0 && record
                      ? `${record.wins}-${record.losses}${record.ties ? `-${record.ties}` : ""}`
                      : `${formatWins(team.mean_wins)} proj. wins`}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex flex-col items-end gap-1">
                      <span className="tabular-nums">{formatPercent(team.playoff_probability)}</span>
                      <AnimatedMeter
                        value={team.playoff_probability}
                        label={`${team.team_name} playoff probability`}
                      />
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex flex-col items-end gap-1">
                      <span className="tabular-nums">{formatPercent(team.title_probability)}</span>
                      <AnimatedMeter
                        value={team.title_probability}
                        label={`${team.team_name} title probability`}
                        glow="accent"
                      />
                    </div>
                  </TableCell>
                  {gamesPlayed > 0 && (
                    <TableCell className="text-right">
                      {diverges ? (
                        <span className="inline-flex items-center gap-1 text-xs font-medium text-destructive">
                          <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                          Record diverges from projected strength
                        </span>
                      ) : null}
                    </TableCell>
                  )}
                </motion.tr>
              );
            })}
          </motion.tbody>
        </Table>
      </div>
    </div>
  );
}
```

Note this drops the `TableBody` import (no longer used — `motion.tbody` replaces it directly, with its `data-slot`/`className` reproduced inline) but keeps `Table`, `TableCell`, `TableHead`, `TableHeader`, `TableRow` (the header row still uses the plain `TableRow`).

- [ ] **Step 2: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build`
Expected: all clean.

Load `/league/<id>` in the dev server:
- Standings rows fade/slide in staggered, after the card itself has animated in (Task 10's outer `Reveal`).
- Playoff %/Title % cells show the existing percentage text plus a small glowing bar underneath that animates from empty to its value.
- The top-ranked row (rank `1`) has a subtle green-tinted left edge.
- Enable reduced motion and reload: rows appear instantly, meters render at full width immediately (no animation).
- Confirm no console errors from `motion.tbody`/`motion.tr` (framer-motion supports arbitrary HTML tags via its `motion` proxy, but verify in the browser console regardless).

- [ ] **Step 3: Commit**

```bash
git add web/components/dashboard/standings-table.tsx
git commit -m "Web: animated playoff/title meters + row stagger on StandingsTable"
```

---

### Task 12: CurrentMatchupCard — hero tilt card

**Files:**
- Modify: `web/components/dashboard/current-matchup-card.tsx`

**Interfaces:**
- Consumes: `TiltCard` (Task 7).
- Produces: same exported `CurrentMatchupCard({ schedule })` signature and the same upcoming/season-complete logic, unchanged. Becomes a Client Component (`TiltCard` requires it), rendered from `page.tsx` with a plain serializable `schedule` prop — same pattern as Task 11.

- [ ] **Step 1: Replace the file**

```tsx
"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { TiltCard } from "@/components/ui/motion";
import type { ScheduleResponse } from "@/lib/types";

/**
 * "Current matchup" for a league with zero completed weeks (see
 * docs/decisions.md Phase 5b): there is no played "current week" to report,
 * so this renders schedule.current_week (the first week with an undecided
 * matchup, computed server-side in sim.api.schedule_view -- never
 * fabricated here) explicitly labeled "Upcoming", not as a result. If every
 * matchup is already decided, current_week is null and this renders a
 * "season complete" state instead of guessing a week number.
 */
export function CurrentMatchupCard({ schedule }: { schedule: ScheduleResponse }) {
  const week = schedule.current_week;
  const matchups = week ? schedule.weeks[week - 1] : [];

  return (
    <TiltCard>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <CardTitle className="font-heading text-base">
            {week ? `Week ${week}` : "Schedule"}
          </CardTitle>
          {week ? (
            <Badge variant="outline" className="border-brand-accent/40 bg-brand-accent/15 text-brand-accent-foreground">
              Upcoming
            </Badge>
          ) : (
            <Badge variant="outline">Season complete</Badge>
          )}
        </CardHeader>
        <CardContent>
          {!week ? (
            <p className="text-sm text-muted-foreground">
              Every matchup in this schedule already has a decided winner.
            </p>
          ) : matchups.length === 0 ? (
            <p className="text-sm text-muted-foreground">No matchups scheduled for this week.</p>
          ) : (
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
          )}
        </CardContent>
      </Card>
    </TiltCard>
  );
}
```

- [ ] **Step 2: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build`
Expected: all clean.

Load `/league/<id>` in the dev server: move the mouse across the current-matchup card and confirm it tilts subtly toward the cursor (max ~4°, no layout shift in neighboring cards) and gains a stronger glow on hover than a plain `Card`. Reduced motion: no tilt, card renders flat and static.

- [ ] **Step 3: Commit**

```bash
git add web/components/dashboard/current-matchup-card.tsx
git commit -m "Web: promote CurrentMatchupCard to the dashboard's tilt hero card"
```

---

### Task 13: RostersGrid — glow hover

**Files:**
- Modify: `web/components/dashboard/rosters-grid.tsx`

**Interfaces:**
- Consumes: `--shadow-glow-primary` (Task 2). No new imports — this is a CSS-only change, stays a Server Component.
- Produces: same exported `RostersGrid({ teams })` signature and the same `<details>`/`<summary>` disclosure behavior.

- [ ] **Step 1: Edit the `<details>` className**

In `web/components/dashboard/rosters-grid.tsx`, find:

```tsx
              className="group rounded-lg border border-border p-3 transition-colors duration-150 open:bg-muted/40 hover:bg-muted/25"
```

Replace with:

```tsx
              className="group rounded-lg border border-border/70 p-3 transition-all duration-150 open:bg-muted/40 hover:bg-primary/5 hover:shadow-[var(--shadow-glow-primary)]"
```

- [ ] **Step 2: Verify**

Run: `cd web && npx tsc --noEmit && npx eslint . && npm run build`
Expected: all clean.

Load `/league/<id>` in the dev server: hover a team's roster entry and confirm a subtle blue glow + tint appears (no layout shift), and that opening a `<details>` (click or `Enter`/`Space` on the `<summary>`) still works exactly as before.

- [ ] **Step 3: Commit**

```bash
git add web/components/dashboard/rosters-grid.tsx
git commit -m "Web: glow hover on RostersGrid's roster entries"
```

---

### Task 14: Documentation — MASTER.md + decisions.md

**Files:**
- Modify: `design-system/MASTER.md`
- Modify: `docs/decisions.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Update `design-system/MASTER.md`'s header line**

Find:

```
**Project:** Fantavo
**Generated:** 2026-08-12 17:39:53
**Category:** Analytics Dashboard
**Design Dials:** Variance 4/10 (Balanced / Modern) | Motion 3/10 (Subtle) | Density 8/10 (Dense / Dashboard)
```

Replace with:

```
**Project:** Fantavo
**Generated:** 2026-08-12 17:39:53
**Category:** Analytics Dashboard
**Design Dials:** Variance 4/10 (Balanced / Modern) | Motion 7/10 (Expressive) | Density 8/10 (Dense / Dashboard)

> **Revised 2026-08-13 (Dark Glass Makeover):** the palette, motion dial, and
> anti-patterns below were updated in place to match
> `docs/superpowers/specs/2026-08-13-dark-glass-makeover-design.md` (approved
> by the project owner) and `docs/decisions.md`'s Phase 14 entry, which
> supersedes this file's original Phase 5a light-theme values. The
> Typography, Spacing, Chart Recommendations, and GSAP scroll-reveal sections
> below are unchanged from Phase 5a.
```

- [ ] **Step 2: Update the Color Palette table**

Find:

```
### Color Palette

| Role | Hex | CSS Variable |
|------|-----|--------------|
| Primary | `#1E40AF` | `--color-primary` |
| On Primary | `#FFFFFF` | `--color-on-primary` |
| Secondary | `#3B82F6` | `--color-secondary` |
| Accent/CTA | `#D97706` | `--color-accent` |
| Background | `#F8FAFC` | `--color-background` |
| Foreground | `#1E3A8A` | `--color-foreground` |
| Muted | `#E9EEF6` | `--color-muted` |
| Border | `#DBEAFE` | `--color-border` |
| Destructive | `#DC2626` | `--color-destructive` |
| Ring | `#1E40AF` | `--color-ring` |

**Color Notes:** Blue data + amber highlights [Accent adjusted from #F59E0B for WCAG 3:1]
```

Replace with:

```
### Color Palette

| Role | Hex | CSS Variable |
|------|-----|--------------|
| Primary | `#2563EB` | `--color-primary` |
| On Primary | `#F8FAFC` | `--color-on-primary` |
| Secondary | `#3B82F6` | `--color-secondary` |
| Accent/CTA | `#39FF14` | `--color-accent` |
| Background | `#090D16` | `--color-background` |
| Foreground | `#E7ECF6` | `--color-foreground` |
| Muted | `#141B2E` | `--color-muted` |
| Border | `#1E293B` | `--color-border` |
| Destructive | `#F87171` | `--color-destructive` |
| Ring | `#60A5FA` | `--color-ring` |

**Color Notes:** Dark obsidian base, glass/translucent surfaces, glow-blue
primary + neon-green accent (rare, high-signal use only). `#39FF14` requires
dark text (`#0B1220`), never white — see
`docs/superpowers/specs/2026-08-13-dark-glass-makeover-design.md`'s contrast
notes.
```

- [ ] **Step 3: Update the Buttons and Cards component specs**

Find:

```
### Buttons

```css
/* Primary Button */
.btn-primary {
  background: #D97706;
  color: white;
  padding: 12px 24px;
  border-radius: 8px;
  font-weight: 600;
  transition: all 200ms ease;
  cursor: pointer;
}

.btn-primary:hover {
  opacity: 0.9;
  transform: translateY(-1px);
}

/* Secondary Button */
.btn-secondary {
  background: transparent;
  color: #1E40AF;
  border: 2px solid #1E40AF;
  padding: 12px 24px;
  border-radius: 8px;
  font-weight: 600;
  transition: all 200ms ease;
  cursor: pointer;
}
```

### Cards

```css
.card {
  background: #F8FAFC;
  border-radius: 12px;
  padding: 24px;
  box-shadow: var(--shadow-md);
  transition: all 200ms ease;
  cursor: pointer;
}

.card:hover {
  box-shadow: var(--shadow-lg);
  transform: translateY(-2px);
}
```
```

Replace with:

```
### Buttons

```css
/* Primary Button -- glow-blue, the default action color */
.btn-primary {
  background: #2563EB;
  color: #F8FAFC;
  padding: 12px 24px;
  border-radius: 8px;
  font-weight: 600;
  box-shadow: 0 0 16px 0 rgba(59, 130, 246, 0.35);
  transition: all 200ms ease;
  cursor: pointer;
}

.btn-primary:hover {
  background: color-mix(in oklch, #2563EB, white 10%);
  box-shadow: 0 0 28px 2px rgba(59, 130, 246, 0.5);
}

/* Accent Button -- neon green, rare/high-signal CTAs only */
.btn-accent {
  background: #39FF14;
  color: #0B1220;
  padding: 12px 24px;
  border-radius: 8px;
  font-weight: 600;
  box-shadow: 0 0 16px 0 rgba(57, 255, 20, 0.3);
  transition: all 200ms ease;
  cursor: pointer;
}
```

### Cards

```css
/* Glass surface: translucent + blurred over the obsidian background */
.card {
  background: rgba(15, 21, 36, 0.7);
  backdrop-filter: blur(12px);
  border-radius: 12px;
  padding: 24px;
  box-shadow: var(--shadow-md);
  transition: all 200ms ease;
  cursor: pointer;
}

.card:hover {
  box-shadow: 0 0 16px 0 rgba(59, 130, 246, 0.35);
  transform: translateY(-2px);
}
```
```

- [ ] **Step 4: Remove "Ornate design" from the Anti-Patterns list**

Find:

```
## Anti-Patterns (Do NOT Use)

- ❌ Ornate design
- ❌ No filtering
```

Replace with:

```
## Anti-Patterns (Do NOT Use)

- ❌ No filtering
```

- [ ] **Step 5: Write the `docs/decisions.md` Phase 14 entry**

Append to the end of `docs/decisions.md`:

```markdown

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
```

- [ ] **Step 6: Verify**

No code to verify — read both files back and confirm the edits landed cleanly (no leftover light-theme values contradicting the new palette, no broken markdown tables).

- [ ] **Step 7: Commit**

```bash
git add design-system/MASTER.md docs/decisions.md
git commit -m "Docs: Phase 14 -- Dark Glass Makeover design system + decisions log"
```

---

### Task 15: Final end-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Full clean build**

Run:
```bash
cd web
npx tsc --noEmit
npx eslint .
npm run build
```
Expected: all three clean, production build succeeds with no warnings introduced by this plan's changes.

- [ ] **Step 2: Browser walkthrough at desktop width**

Start the dev server, open `/league/<a real or synthetic leagueId>`:
- Obsidian background, glass cards throughout (not just the dashboard — confirm a couple of the *other* 10 pages, e.g. `/power-rankings` and `/roast`, also render with the new dark palette and glass `Card`/`Button`/`Table`, proving the token-cascade claim).
- On the dashboard specifically: four cards stagger in on load; standings meters animate; top-ranked row has the green glow edge; the current-matchup card tilts toward the cursor and glows on hover; roster entries glow on hover and still expand/collapse correctly.
- Sidebar: all 11 icons present, correct active-icon glow on each page, tooltips appear on hover/focus, keyboard tab order reaches every icon with a visible focus ring.

- [ ] **Step 3: Browser walkthrough at 375px**

`resize_window` to the mobile preset, reload `/league/<id>`:
- `document.documentElement.scrollWidth === document.documentElement.clientWidth` (no horizontal scroll).
- Sidebar rail still usable (56px wide, doesn't crowd out content).
- Dashboard cards stack in a single column, still legible.

- [ ] **Step 4: Reduced motion**

Enable `prefers-reduced-motion: reduce` (OS setting or `resize_window`'s `colorScheme`/devtools emulation), reload the dashboard:
- No stagger, no tilt, no meter fill animation — everything renders instantly, fully in its end state.

- [ ] **Step 5: Contrast spot-check**

In devtools, inspect computed colors for: body text on `--background`, `--muted-foreground` on `--background`, a `Badge` using `brand-accent`, and the default `Button`. Confirm each matches the hex pairs documented in Task 14's decisions.md entry (or re-run the WCAG contrast check if anything was substituted along the way).

- [ ] **Step 6: Final commit (if any fixes were needed)**

If Steps 1–5 required any fixes, commit them individually with descriptive messages before considering this plan complete. If everything passed as implemented, no further commit is needed — the work is already committed task-by-task.
