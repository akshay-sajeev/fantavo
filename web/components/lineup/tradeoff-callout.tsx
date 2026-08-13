import { Scale } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import type { LineupTradeoff } from "@/lib/lineup-optimizer";

/**
 * The headline sentence leads the page, same "lead with the finding, not a
 * wall of numbers" treatment RecommendationCallout (Playoff Planner) and
 * StructuralFindingCard (Draft Autopsy) already established -- here making
 * explicit why a rational manager might pick either Safest or Highest
 * upside, per PLAN.md ("the UI should show why someone would pick either
 * one, not just present two lists of names"). The sentence itself is pure
 * arithmetic over already-returned numbers (lib/lineup-optimizer.ts), never
 * computed here.
 */
export function TradeoffCallout({ tradeoff }: { tradeoff: LineupTradeoff }) {
  return (
    <Card className="border-l-4 border-l-primary bg-primary/5">
      <CardContent className="flex items-start gap-2.5">
        <Scale className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
        <div className="space-y-1.5">
          <p className="text-xs font-semibold tracking-wide text-primary uppercase">
            The tradeoff
          </p>
          <p className="font-heading text-base leading-relaxed font-medium text-foreground">
            {tradeoff.headline}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
