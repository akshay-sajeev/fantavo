import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BestWorstPicks } from "@/components/draft/best-worst-picks";
import { PositionGradeStrip } from "@/components/draft/position-grade-strip";
import { StructuralFindingCard } from "@/components/draft/structural-finding-card";
import type { TeamDraftAutopsy } from "@/lib/types";

/**
 * One team's full draft report: the structural finding leads (largest,
 * topmost element), then the best/worst decision callouts, then the
 * positional-grade summary strip. The full pick-by-pick board is
 * deliberately NOT repeated here -- it lives once, league-wide, in
 * DraftBoardGrid below this grid of cards, so per-pick data never crowds
 * out the narrative per team.
 */
export function TeamDraftReportCard({ team }: { team: TeamDraftAutopsy }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-base">{team.team_name}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <StructuralFindingCard team={team} />
        <BestWorstPicks best={team.best_pick} worst={team.worst_pick} />
        <PositionGradeStrip grades={team.position_grades} />
      </CardContent>
    </Card>
  );
}
