import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RecommendationCallout } from "@/components/playoffs/recommendation-callout";
import { SlotStrengthBar } from "@/components/playoffs/slot-strength-bar";
import { formatPercent } from "@/lib/format";
import type { PlayoffSeedOdds, TeamPlayoffPlan } from "@/lib/types";

/**
 * One team's Playoff Planner: the recommendation leads (largest, topmost
 * element, per PLAN.md's "lead with the action, not the data"), then the
 * per-slot playoff-weeks strength breakdown -- the same
 * narrative-then-supporting-data layout Draft Autopsy's
 * `TeamDraftReportCard` already established for this app. The weakest slot
 * (the one the recommendation is about, when a real weakness exists) is
 * shown first among the slots.
 */
export function TeamPlayoffCard({
  team,
  seeding,
}: {
  team: TeamPlayoffPlan;
  seeding: PlayoffSeedOdds | undefined;
}) {
  const orderedSlots = [...team.slot_strengths].sort((a, b) => {
    if (a.slot_label === team.weakest_slot) return -1;
    if (b.slot_label === team.weakest_slot) return 1;
    return 0;
  });

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <CardTitle className="font-heading text-base">{team.team_name}</CardTitle>
        {seeding && (
          <div className="flex shrink-0 gap-3 text-right text-xs text-muted-foreground">
            <span>
              Playoffs{" "}
              <span className="font-semibold text-foreground tabular-nums">
                {formatPercent(seeding.playoff_probability, 0)}
              </span>
            </span>
            <span>
              Title{" "}
              <span className="font-semibold text-foreground tabular-nums">
                {formatPercent(seeding.title_probability, 0)}
              </span>
            </span>
          </div>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        <RecommendationCallout recommendation={team.recommendation} />

        {orderedSlots.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              Playoff-weeks strength by slot
            </p>
            <div className="divide-y divide-border">
              {orderedSlots.map((slot) => (
                <SlotStrengthBar key={slot.slot_label} slot={slot} />
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
