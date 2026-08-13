/**
 * Shared, presentation-only mappings from an already-computed API value to a
 * Tailwind className / short label -- the same class of thing as
 * `web/components/risk/team-risk-card.tsx`'s `riskBand()`. No new numbers
 * are computed here, only how an existing one is colored and labeled.
 */

/** Banding for `PositionGrade.label` ("Above the field" / "Below the field" /
 * "On par with the field"), a string the API already computed. */
export function gradeLabelClassName(label: string): string {
  if (label === "Above the field") {
    return "bg-primary/10 text-primary border-primary/25";
  }
  if (label === "Below the field") {
    return "bg-destructive/10 text-destructive border-destructive/30";
  }
  return "bg-muted text-muted-foreground border-border";
}

/** Sign-based coloring for a raw `value_gap` number: positive (took the
 * better same-position player) reads as favorable, negative (a better
 * alternative was passed over) reads as unfavorable. */
export function valueGapClassName(valueGap: number): string {
  if (valueGap > 0) return "text-primary";
  if (valueGap < 0) return "text-destructive";
  return "text-muted-foreground";
}

export function formatValueGap(valueGap: number): string {
  const sign = valueGap > 0 ? "+" : "";
  return `${sign}${valueGap.toFixed(1)}`;
}
