import { InjuryBadge } from "@/components/waivers/injury-badge";
import { formatPercent, formatPercentPoints, formatPoints } from "@/lib/format";
import type { WaiverCandidate } from "@/lib/types";

/**
 * One ranked free-agent candidate within a position group. The reasoning
 * sentence (Opportunity + Availability, server-synthesized) leads under the
 * player's name -- the group card above already carries the League
 * fit/Competition half of "why this matters for this team" (see
 * `PositionGroupCard`), so together the group header and this row satisfy
 * PLAN.md's "every entry says why it matters for this specific roster"
 * without repeating the identical rival-team sentence on every single row.
 * Supporting numbers (opportunity/ownership/points) follow the narrative,
 * per this app's established narrative-leads-data-follows layout (Draft
 * Autopsy, Playoff Planner).
 */
export function CandidateRow({ candidate, rank }: { candidate: WaiverCandidate; rank: number }) {
  return (
    <li className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="w-5 shrink-0 text-right text-xs font-semibold tabular-nums text-muted-foreground">
            {rank}
          </span>
          <span className="truncate font-medium text-foreground">{candidate.player_name}</span>
          <InjuryBadge status={candidate.injury_status} />
        </div>
        <div className="shrink-0 text-right">
          <div className="font-heading text-sm font-semibold tabular-nums text-foreground">
            {formatPoints(candidate.expected_playable_points)} pts
          </div>
          <div className="text-[11px] text-muted-foreground">realistic weekly value</div>
        </div>
      </div>

      <p className="pl-7 text-xs leading-relaxed text-foreground/80">{candidate.reasoning}</p>

      <div className="flex flex-wrap gap-x-4 gap-y-1 pl-7 text-[11px] text-muted-foreground">
        <span>
          Opportunity{" "}
          <span className="font-medium tabular-nums text-foreground">
            {formatPercent(candidate.opportunity_score, 0)}
          </span>
        </span>
        <span>
          Owned{" "}
          <span className="font-medium tabular-nums text-foreground">
            {formatPercentPoints(candidate.percent_owned, 0)}
          </span>
        </span>
        <span>
          Projects{" "}
          <span className="font-medium tabular-nums text-foreground">
            {formatPoints(candidate.mean_points_per_game)} pts/gm
          </span>
        </span>
        {candidate.average_draft_position !== null && (
          <span>
            ADP{" "}
            <span className="font-medium tabular-nums text-foreground">
              {candidate.average_draft_position.toFixed(0)}
            </span>
          </span>
        )}
      </div>
    </li>
  );
}
