# Design System Master File

> **LOGIC:** When building a specific page, first check `design-system/pages/[page-name].md`.
> If that file exists, its rules **override** this Master file.
> If not, strictly follow the rules below.

---

**Project:** Fantavo
**Generated:** 2026-08-12 17:39:53
**Category:** Analytics Dashboard
**Design Dials:** Variance 4/10 (Balanced / Modern) | Motion 3/10 (Subtle) | Density 8/10 (Dense / Dashboard)

---

## Global Rules

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

### Typography

- **Heading Font:** Fira Code
- **Body Font:** Fira Sans
- **Mood:** dashboard, data, analytics, code, technical, precise
- **Google Fonts:** [Fira Code + Fira Sans](https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap)

**CSS Import:**
```css
@import url('https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap');
```

### Spacing Variables

*Density: 8/10 — Dense / Dashboard*

| Token | Value | Usage |
|-------|-------|-------|
| `--space-xs` | `2px` / `0.125rem` | Tight gaps |
| `--space-sm` | `4px` / `0.25rem` | Icon gaps, inline spacing |
| `--space-md` | `8px` / `0.5rem` | Standard padding |
| `--space-lg` | `12px` / `0.75rem` | Section padding |
| `--space-xl` | `16px` / `1rem` | Large gaps |
| `--space-2xl` | `24px` / `1.5rem` | Section margins |
| `--space-3xl` | `32px` / `2rem` | Hero padding |

### Shadow Depths

| Level | Value | Usage |
|-------|-------|-------|
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.05)` | Subtle lift |
| `--shadow-md` | `0 4px 6px rgba(0,0,0,0.1)` | Cards, buttons |
| `--shadow-lg` | `0 10px 15px rgba(0,0,0,0.1)` | Modals, dropdowns |
| `--shadow-xl` | `0 20px 25px rgba(0,0,0,0.15)` | Hero images, featured cards |

---

## Component Specs

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

### Inputs

```css
.input {
  padding: 12px 16px;
  border: 1px solid #E2E8F0;
  border-radius: 8px;
  font-size: 16px;
  transition: border-color 200ms ease;
}

.input:focus {
  border-color: #1E40AF;
  outline: none;
  box-shadow: 0 0 0 3px #1E40AF20;
}
```

### Modals

```css
.modal-overlay {
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
}

.modal {
  background: white;
  border-radius: 16px;
  padding: 32px;
  box-shadow: var(--shadow-xl);
  max-width: 500px;
  width: 90%;
}
```

---

## Style Guidelines

**Style:** Predictive Analytics

**Keywords:** Forecast lines, confidence intervals, trend projections, scenario modeling, AI-driven insights, anomaly detection visualization

**Best For:** Forecasting dashboards, anomaly detection systems, trend prediction dashboards, AI-powered analytics, budget planning

**Key Effects:** Forecast line animation on draw, confidence band fade-in, anomaly pulse alert, smoothing function animations

### Page Pattern

**Pattern Name:** AI Personalization Landing

- **Conversion Strategy:** 20%+ conversion with personalization. Requires analytics integration. Fallback for new users.
- **CTA Placement:** Context-aware placement based on user segment
- **Section Order:** 1. Dynamic hero (personalized), 2. Relevant features, 3. Tailored testimonials, 4. Smart CTA

> **Editorial note (not generator output):** the `--design-system` command always
> fills in a "Page Pattern" from the tool's landing-page domain, even for pure
> app dashboards. Fantavo has no marketing/conversion landing page — it's a
> directly-authenticated weekly-use tool — so this Pattern section does not
> govern 5b. Follow the Style, Color, Typography, Component Specs and Chart
> Recommendations sections instead; disregard "Conversion Strategy" / "CTA
> Placement" / hero-testimonial section ordering here.

---

## Chart Recommendations — Probability & Ranked Comparisons

Queried separately via `--domain chart`, since this app's core visual problem
is showing the *shape* of a probability, not a bare percentage (see CLAUDE.md:
"Distributions, not point estimates"). Two query passes, summarized below —
one for the probability/distribution problem (championship odds, playoff odds,
finish distributions, confidence ranges), one for the ranked-comparison problem
(power rankings).

### Probability distributions & confidence ranges

| Chart | Best chart type | Use for | Color guidance | Library | A11y fallback |
|---|---|---|---|---|---|
| **Box Plot** (Distribution/Statistical) | Box plot, secondary: Violin, Beeswarm | Finish-position spread per team (min/Q1/median/Q3/max across sim outcomes) — needs ≥20 sample points per group, which `simulate_seasons()`'s n_sims easily clears | Box fill `#BBDEFB`, border `#1976D2`, median line `#D32F2F` bold, outliers `#F44336` | Plotly, D3.js, Chart.js (plugin) | Stats summary table (min/Q1/median/Q3/max/mean) + outlier count annotated in the subtitle |
| **Line with Confidence Band** (Time-Series Forecast) | Line + shaded band, secondary: Ribbon Chart | Any week-over-week or season-trajectory probability (e.g. title-odds trend as the season progresses) | Actual: solid `#0080FF`. Forecast: dashed `#FF9500`. Confidence band: 15% opacity fill, same hue | Chart.js, ApexCharts, Plotly | Toggle actual/forecast independently; legend must distinguish by line-style (solid vs dashed), not color alone |

Applied to this app's specific probability surfaces:
- **Championship / playoff odds** — a bare percentage is explicitly disallowed
  by PLAN.md ("21% title chance with a wide finish distribution means
  something different from 21% with a narrow one"). Pair the number with a
  compact box-plot or a small histogram-style sparkline of the underlying
  finish distribution, not the percentage alone.
- **Finish distributions** — per-team distribution over discrete final
  standings (1st..Nth). A box plot (median finish, quartile spread, outlier
  finishes) is the generator's direct recommendation for this shape and reads
  well at dashboard density; a per-rank stacked/horizontal bar (probability
  mass at each finish position) is the natural secondary view when the
  discreteness of "finish place" needs to be legible rather than smoothed into
  a continuous spread — build both as options for 5b to choose per placement,
  not as two competing implementations.
- **Confidence ranges** (e.g. projected score ranges, floor/ceiling) — Line
  with Confidence Band pattern, using the same actual-vs-forecast /
  dashed-band visual language as the rest of the Predictive Analytics style.

### Ranked comparisons (power rankings)

| Chart | Best chart type | Use for | Color guidance | Library | A11y fallback |
|---|---|---|---|---|---|
| **Bar Chart** (Compare Categories) | Horizontal or vertical bar, secondary: Grouped Bar | Power rankings ordered by simulated title probability, ≤15 teams (this app's leagues are 10-12 teams — comfortably inside the horizontal-bar threshold) | Each bar a distinct color; always sort descending by value | Chart.js, Recharts, D3.js | Value labels always visible on each bar (not hover-only); provide a sort control and CSV export |

Applied to power rankings specifically: sort descending by simulated title
probability (never a frontend-computed score — see CLAUDE.md's "no analytics
logic in components" rule), show the value label on the bar itself, and pair
each bar with the playoff-odds and finish-distribution treatments above rather
than a bare number.

**Stack integration (shadcn/ui):** wrap Recharts in shadcn's `Chart` component
family (`ChartContainer` + `chartConfig`, not a raw `<ResponsiveContainer>`),
theme via `chartConfig` color definitions rather than inline `fill` props, and
use `ChartTooltip` + `ChartTooltipContent` instead of Recharts' own `Tooltip` —
per `--stack shadcn` guidance. Ref: https://ui.shadcn.com/docs/components/chart

---

## Motion

**Scroll Reveal** (Subtle) — Trigger: scroll (viewport enter) | Duration: 300-400ms | Easing: `power1.out`

```js
gsap.from(el, { opacity: 0, y: 12, duration: 0.35, ease: 'power1.out', scrollTrigger: { trigger: el, start: 'top 90%', toggleActions: 'play none none reverse' } });
```

**Framework notes:** Requires the ScrollTrigger plugin registered once via gsap.registerPlugin(ScrollTrigger)

- ✅ Keep the y offset small (8-16px) so it reads as a fade, not a slide
- ❌ Don't reveal below-the-fold content needed for SEO/crawlers as invisible-by-default without a no-JS fallback
- ⚡ toggleActions 'play none none reverse' avoids re-triggering on every scroll direction change

---

## Anti-Patterns (Do NOT Use)

- ❌ Ornate design
- ❌ No filtering

### Additional Forbidden Patterns

- ❌ **Emojis as icons** — Use SVG icons (Heroicons, Lucide, Simple Icons)
- ❌ **Missing cursor:pointer** — All clickable elements must have cursor:pointer
- ❌ **Layout-shifting hovers** — Avoid scale transforms that shift layout
- ❌ **Low contrast text** — Maintain 4.5:1 minimum contrast ratio
- ❌ **Instant state changes** — Always use transitions (150-300ms)
- ❌ **Invisible focus states** — Focus states must be visible for a11y

---

## Pre-Delivery Checklist

Before delivering any UI code, verify:

- [ ] No emojis used as icons (use SVG instead)
- [ ] All icons from consistent icon set (Heroicons/Lucide)
- [ ] `cursor-pointer` on all clickable elements
- [ ] Hover states with smooth transitions (150-300ms)
- [ ] Light mode: text contrast 4.5:1 minimum
- [ ] Focus states visible for keyboard navigation
- [ ] `prefers-reduced-motion` respected
- [ ] Responsive: 375px, 768px, 1024px, 1440px
- [ ] No content hidden behind fixed navbars
- [ ] No horizontal scroll on mobile
