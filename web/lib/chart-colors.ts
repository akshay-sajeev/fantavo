/**
 * Categorical palette for per-team chart series (power rankings bars,
 * finish-distribution strips). design-system/MASTER.md specifies role
 * colors (primary blue, secondary blue, accent amber, destructive red) but
 * not a full categorical sweep for an arbitrary N-team league, so this
 * extends those hues into a larger, distinct-per-team sequence -- documented
 * in docs/decisions.md Phase 5b. Comfortably covers this app's league sizes
 * (10-12 teams per CLAUDE.md's chart-recommendation note).
 */
export const TEAM_CHART_COLORS: readonly string[] = [
  "#1E40AF", // primary blue
  "#D97706", // brand accent amber
  "#3B82F6", // data blue
  "#0EA5A4", // teal
  "#7C3AED", // violet
  "#DC2626", // destructive red
  "#059669", // green
  "#DB2777", // pink
  "#CA8A04", // gold
  "#4F46E5", // indigo
  "#0891B2", // cyan
  "#EA580C", // orange
];

export function teamColor(index: number): string {
  return TEAM_CHART_COLORS[index % TEAM_CHART_COLORS.length];
}
