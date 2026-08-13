import type { SimulationResponse, TeamOutcome } from "@/lib/types";

/**
 * Pure arithmetic over two already-computed SimulationResponses -- no new
 * probability model, no weighting, nothing simulate_seasons() computes
 * differently. Same class of operation as lib/standings.ts's tally of
 * already-decided winners (see docs/decisions.md Phase 5b): bookkeeping
 * over API-returned facts for display, not analytics logic.
 */
export interface OutcomeDelta {
  teamId: number;
  teamName: string;
  before: TeamOutcome;
  after: TeamOutcome;
  /** after.title_probability - before.title_probability, in percentage points. */
  titleDeltaPts: number;
  /** after.playoff_probability - before.playoff_probability, in percentage points. */
  playoffDeltaPts: number;
}

function byTeamId(sim: SimulationResponse): Map<number, TeamOutcome> {
  return new Map(sim.teams.map((t) => [t.team_id, t]));
}

export function computeOutcomeDeltas(
  before: SimulationResponse,
  after: SimulationResponse,
  teamIds: number[],
): OutcomeDelta[] {
  const beforeById = byTeamId(before);
  const afterById = byTeamId(after);
  return teamIds.map((teamId) => {
    const b = beforeById.get(teamId);
    const a = afterById.get(teamId);
    if (!b || !a) {
      throw new Error(`team ${teamId} is missing from a whatif comparison response`);
    }
    return {
      teamId,
      teamName: a.team_name,
      before: b,
      after: a,
      titleDeltaPts: (a.title_probability - b.title_probability) * 100,
      playoffDeltaPts: (a.playoff_probability - b.playoff_probability) * 100,
    };
  });
}

/** Threshold below which a delta reads as "no meaningful change" rather
 * than a real swing -- a display threshold for wording, not a statistical
 * significance test. */
const NEGLIGIBLE_PTS = 0.5;

/**
 * The headline asymmetry sentence a two-team trade needs front and center
 * (PLAN.md: "A trade that helps you 4 points and your opponent 9 should say
 * so plainly and visibly"). Built from title-probability deltas already
 * computed above -- plain comparison/wording logic, not a new metric.
 */
export function describeTradeAsymmetry(a: OutcomeDelta, b: OutcomeDelta): string {
  const magA = Math.abs(a.titleDeltaPts);
  const magB = Math.abs(b.titleDeltaPts);

  if (magA < NEGLIGIBLE_PTS && magB < NEGLIGIBLE_PTS) {
    return `Negligible title-odds impact for both ${a.teamName} and ${b.teamName}.`;
  }

  const sameDirection = Math.sign(a.titleDeltaPts) === Math.sign(b.titleDeltaPts);

  if (!sameDirection) {
    const winner = a.titleDeltaPts > b.titleDeltaPts ? a : b;
    const loser = a.titleDeltaPts > b.titleDeltaPts ? b : a;
    return (
      `This trade favors ${winner.teamName} (title odds ${winner.titleDeltaPts >= 0 ? "+" : ""}` +
      `${winner.titleDeltaPts.toFixed(1)} pts) at ${loser.teamName}'s expense ` +
      `(${loser.titleDeltaPts >= 0 ? "+" : ""}${loser.titleDeltaPts.toFixed(1)} pts).`
    );
  }

  const [bigger, smaller] = magA >= magB ? [a, b] : [b, a];
  if (magA < NEGLIGIBLE_PTS || magB < NEGLIGIBLE_PTS) {
    return `${bigger.teamName} sees the entire benefit here -- ${smaller.teamName}'s title odds barely move.`;
  }
  const ratio = Math.abs(bigger.titleDeltaPts) / Math.abs(smaller.titleDeltaPts);
  const direction = bigger.titleDeltaPts >= 0 ? "gains" : "loses";
  return (
    `${bigger.teamName} ${direction} roughly ${ratio.toFixed(1)}x more title equity than ` +
    `${smaller.teamName} from this trade.`
  );
}
