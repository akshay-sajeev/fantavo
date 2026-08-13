import { ArrowRight, TrendingDown, TrendingUp } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { FinishDistributionStrip } from "@/components/shared/finish-distribution-strip";
import { formatPercent, formatSignedPoints } from "@/lib/format";
import { computeOutcomeDeltas, describeTradeAsymmetry, type OutcomeDelta } from "@/lib/whatif-compare";
import type { WhatIfCompareResponse } from "@/lib/types";

/** Shown while the two live n_sims=2000 whatif calls are in flight -- PLAN.md
 * requires a loading state, this is it. Sized to roughly match the results
 * layout below so nothing jumps once data arrives. */
export function WhatIfLoadingSkeleton({ teamCount = 2 }: { teamCount?: number }) {
  return (
    <div className="space-y-3" aria-live="polite" aria-busy="true">
      <Skeleton className="h-16 w-full rounded-lg" />
      <div className={`grid grid-cols-1 gap-3 ${teamCount > 1 ? "sm:grid-cols-2" : ""}`}>
        {Array.from({ length: teamCount }).map((_, i) => (
          <Skeleton key={i} className="h-40 w-full rounded-lg" />
        ))}
      </div>
      <p className="text-center text-xs text-muted-foreground">
        Running 2,000 simulated seasons for this scenario&hellip;
      </p>
    </div>
  );
}

function DeltaBadge({ points }: { points: number }) {
  if (Math.abs(points) < 0.05) {
    return <span className="text-xs text-muted-foreground">no change</span>;
  }
  const positive = points > 0;
  const Icon = positive ? TrendingUp : TrendingDown;
  return (
    <span
      className={
        "inline-flex items-center gap-1 text-xs font-semibold tabular-nums " +
        (positive ? "text-primary" : "text-destructive")
      }
    >
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      {formatSignedPoints(points)}
    </span>
  );
}

export function TeamOutcomeCard({ delta }: { delta: OutcomeDelta }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-base">{delta.teamName}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm text-muted-foreground">Title odds</span>
          <span className="flex items-center gap-2 tabular-nums">
            <span className="text-muted-foreground">{formatPercent(delta.before.title_probability)}</span>
            <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            <span className="font-semibold">{formatPercent(delta.after.title_probability)}</span>
            <DeltaBadge points={delta.titleDeltaPts} />
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm text-muted-foreground">Playoff odds</span>
          <span className="flex items-center gap-2 tabular-nums">
            <span className="text-muted-foreground">{formatPercent(delta.before.playoff_probability)}</span>
            <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            <span className="font-semibold">{formatPercent(delta.after.playoff_probability)}</span>
            <DeltaBadge points={delta.playoffDeltaPts} />
          </span>
        </div>
        <div>
          <p className="mb-1 text-xs text-muted-foreground">Finish distribution after this scenario</p>
          <FinishDistributionStrip finishDistribution={delta.after.finish_distribution} />
        </div>
      </CardContent>
    </Card>
  );
}

export function TradeComparisonResult({
  compare,
  teamAId,
  teamBId,
}: {
  compare: WhatIfCompareResponse;
  teamAId: number;
  teamBId: number;
}) {
  const [deltaA, deltaB] = computeOutcomeDeltas(compare.before, compare.after, [teamAId, teamBId]);
  const asymmetry = describeTradeAsymmetry(deltaA, deltaB);

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-brand-accent/40 bg-brand-accent/10 p-4">
        <p className="font-heading text-base font-semibold text-foreground">{asymmetry}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Based on 2,000 live simulated seasons per side, seed {compare.seed} (same seed for
          before and after, so the difference is the trade, not sampling noise).
        </p>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <TeamOutcomeCard delta={deltaA} />
        <TeamOutcomeCard delta={deltaB} />
      </div>
    </div>
  );
}

export function SingleTeamComparisonResult({
  compare,
  teamId,
}: {
  compare: WhatIfCompareResponse;
  teamId: number;
}) {
  const [delta] = computeOutcomeDeltas(compare.before, compare.after, [teamId]);
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Based on 2,000 live simulated seasons per side, seed {compare.seed}.
      </p>
      <TeamOutcomeCard delta={delta} />
    </div>
  );
}
