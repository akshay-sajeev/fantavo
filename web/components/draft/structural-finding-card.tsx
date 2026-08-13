import { Card, CardContent } from "@/components/ui/card";
import type { TeamDraftAutopsy } from "@/lib/types";

/**
 * The structural finding, given the visual weight PLAN.md asks for: the
 * first thing on a team's draft report, styled as a headline callout, not a
 * caption under a table. The sentence itself is entirely server-computed
 * (sim/api/draft_autopsy_view.py's `_synthesize_structural_finding`) --
 * this component only lays it out.
 */
export function StructuralFindingCard({ team }: { team: TeamDraftAutopsy }) {
  return (
    <Card className="border-l-4 border-l-primary bg-primary/5">
      <CardContent className="space-y-1.5">
        <p className="text-xs font-semibold tracking-wide text-primary uppercase">
          Structural finding
        </p>
        <p className="font-heading text-base leading-relaxed font-medium text-foreground">
          {team.structural_finding}
        </p>
      </CardContent>
    </Card>
  );
}
