/** Pure display formatting -- no probability math, no rankings, no derived
 * stats. Every number these functions touch was already computed by the
 * API; this file only decides how many decimal places to print. */

export function formatPercent(fraction: number, digits = 1): string {
  return `${(fraction * 100).toFixed(digits)}%`;
}

export function formatPoints(points: number, digits = 1): string {
  return points.toFixed(digits);
}

export function formatWins(wins: number, digits = 1): string {
  return wins.toFixed(digits);
}

const PLACE_SUFFIXES: Record<number, string> = { 1: "st", 2: "nd", 3: "rd" };

/** 0-indexed API place -> a 1-indexed ordinal label ("1st", "2nd", ...). */
export function ordinal(zeroIndexedPlace: number): string {
  const n = zeroIndexedPlace + 1;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
  return `${n}${PLACE_SUFFIXES[n % 10] ?? "th"}`;
}
