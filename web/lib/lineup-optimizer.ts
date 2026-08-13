import type { LineupProjection } from "@/lib/types";

/**
 * Pure arithmetic over three already-computed LineupProjections -- no new
 * probability model, no weighting, nothing sim.engine.simulate_seasons()
 * computes differently. Same class of operation as lib/whatif-compare.ts's
 * describeTradeAsymmetry (see docs/decisions.md Phase 6): a natural-
 * language sentence built entirely from deltas the API already returned,
 * kept in lib/ rather than a component per CLAUDE.md's "no analytics logic
 * in components" rule.
 */

const NEGLIGIBLE_TITLE_PTS = 0.3;
const NEGLIGIBLE_FLOOR_PTS = 0.5;

export interface LineupTradeoff {
  /** safest.weekly_floor - current.weekly_floor, in points. */
  safestFloorDeltaPts: number;
  /** current.title_probability - safest.title_probability, in percentage points
   * (the price safest pays in title equity, if any). */
  safestTitleCostPts: number;
  /** highest_upside.title_probability - current.title_probability, in percentage points. */
  upsideTitleDeltaPts: number;
  /** current.weekly_floor - highest_upside.weekly_floor, in points (the
   * downside upside gives back, if any). */
  upsideFloorCostPts: number;
  /** True when safest and highest_upside are literally the same candidate
   * lineup (a real, valid outcome -- see sim.api.lineup_optimizer_view: not
   * every roster has a real safety/upside tradeoff to make). */
  sameLineup: boolean;
  headline: string;
}

function sameCandidate(a: LineupProjection, b: LineupProjection): boolean {
  if (a.assignments.length !== b.assignments.length) return false;
  const aIds = new Set(a.assignments.map((x) => x.player_id));
  return b.assignments.every((x) => aIds.has(x.player_id));
}

export function describeLineupTradeoff(
  current: LineupProjection,
  safest: LineupProjection,
  upside: LineupProjection,
): LineupTradeoff {
  const safestFloorDeltaPts = safest.weekly_floor - current.weekly_floor;
  const safestTitleCostPts = (current.title_probability - safest.title_probability) * 100;
  const upsideTitleDeltaPts = (upside.title_probability - current.title_probability) * 100;
  const upsideFloorCostPts = current.weekly_floor - upside.weekly_floor;
  const sameLineup = sameCandidate(safest, upside);

  let headline: string;
  const safestHelps = safestFloorDeltaPts > NEGLIGIBLE_FLOOR_PTS;
  const upsideHelps = upsideTitleDeltaPts > NEGLIGIBLE_TITLE_PTS;

  if (sameLineup) {
    headline = safestHelps || upsideHelps
      ? "The same single swap improves both your floor and your title odds -- no tradeoff to make here."
      : "Your current lineup is already both the safest legal option and the one with the best title odds -- no swap available beats it on either measure.";
  } else if (!safestHelps && !upsideHelps) {
    headline =
      "No single-slot swap meaningfully improves your floor or your title odds -- your current lineup already looks like the right call.";
  } else if (safestHelps && !upsideHelps) {
    headline = `Only the safest lineup actually helps: +${safestFloorDeltaPts.toFixed(1)} pts to your weekly floor, with no real title-odds gain available from a single swap.`;
  } else if (!safestHelps && upsideHelps) {
    headline = `Only the highest-upside lineup actually helps: +${upsideTitleDeltaPts.toFixed(1)} pts of title odds, with no real floor gain available from a single swap.`;
  } else {
    headline =
      `Safest raises your weekly floor by ${safestFloorDeltaPts.toFixed(1)} pts` +
      (safestTitleCostPts > NEGLIGIBLE_TITLE_PTS
        ? ` at a cost of ${safestTitleCostPts.toFixed(1)} pts of title odds`
        : "") +
      `. Highest upside adds ${upsideTitleDeltaPts.toFixed(1)} pts of title odds` +
      (upsideFloorCostPts > NEGLIGIBLE_FLOOR_PTS
        ? ` at a cost of ${upsideFloorCostPts.toFixed(1)} pts off your weekly floor`
        : "") +
      " -- two different bets on the same roster.";
  }

  return {
    safestFloorDeltaPts,
    safestTitleCostPts,
    upsideTitleDeltaPts,
    upsideFloorCostPts,
    sameLineup,
    headline,
  };
}
