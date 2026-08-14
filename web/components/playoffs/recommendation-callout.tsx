import { Target } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

/**
 * The action leads the page, per PLAN.md's "lead with the action, not the
 * data" instruction -- the same visual weight `StructuralFindingCard`
 * (Draft Autopsy) gives its narrative, styled with the brand accent instead
 * of primary so it reads distinctly as a call to action rather than a
 * finding. The sentence itself is entirely server-computed
 * (sim.api.playoff_planner_view._synthesize_recommendation) -- this
 * component only lays it out.
 */
export function RecommendationCallout({ recommendation }: { recommendation: string }) {
  return (
    <Card className="border-l-4 border-l-brand-accent bg-brand-accent/5">
      <CardContent className="flex items-start gap-2.5">
        <Target className="mt-0.5 h-4 w-4 shrink-0 text-brand-accent" aria-hidden="true" />
        <div className="space-y-1.5">
          <p className="text-xs font-semibold tracking-wide text-brand-accent uppercase">
            Do this now
          </p>
          <p className="font-heading text-base leading-relaxed font-medium text-foreground">
            {recommendation}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
