import { ShieldAlert } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { formatPercent } from "@/lib/format";
import type { RivalThreat } from "@/lib/types";

/**
 * The biggest threat, given the visual weight PLAN.md's UI bullet asks for
 * ("threat cards per rival... surfaced clearly, not buried in a table") --
 * destructive-tinted (the one color this app's palette reserves for
 * risk/negative framing, see docs/decisions.md Phase 5b's token notes),
 * mirroring the same headline-callout layout `StructuralFindingCard` /
 * `RecommendationCallout` already established for this app's other
 * server-synthesized narratives. The reasoning sentence is entirely
 * server-computed (sim.api.beat_my_league_view) -- this component only lays
 * it out. Per the UX guideline against color-only signaling, the icon and
 * "Biggest threat" label carry the same meaning the destructive tint does.
 */
export function ThreatCard({ threat }: { threat: RivalThreat }) {
  return (
    <Card className="border-l-4 border-l-destructive bg-destructive/5">
      <CardContent className="space-y-2">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-1.5">
            <ShieldAlert className="h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
            <p className="text-xs font-semibold tracking-wide text-destructive uppercase">
              Biggest threat
            </p>
          </div>
          <span className="shrink-0 text-right text-xs text-muted-foreground">
            Title odds{" "}
            <span className="font-semibold text-destructive tabular-nums">
              {formatPercent(threat.title_probability, 1)}
            </span>
          </span>
        </div>
        <p className="font-heading text-base leading-snug font-semibold text-foreground">
          {threat.team_name}
        </p>
        <p className="text-sm leading-relaxed text-foreground/90">{threat.reasoning}</p>
      </CardContent>
    </Card>
  );
}
