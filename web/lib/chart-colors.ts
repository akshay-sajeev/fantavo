/**
 * Categorical palette for per-team chart series (power rankings bars,
 * finish-distribution strips). docs/MASTER.md specifies role
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
