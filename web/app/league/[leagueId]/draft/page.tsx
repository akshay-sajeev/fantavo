import { getDraftAutopsy } from "@/lib/api";
import { getSessionToken } from "@/lib/auth";
import { ApiErrorPanel } from "@/components/shared/api-error-panel";
import { DraftBoardGrid } from "@/components/draft/draft-board-grid";
import { TeamDraftReportCard } from "@/components/draft/team-draft-report-card";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default async function DraftAutopsyPage({
  params,
}: {
  params: Promise<{ leagueId: string }>;
}) {
  const { leagueId } = await params;
  const id = Number(leagueId);
  const token = (await getSessionToken())!;

  let autopsy;
  try {
    autopsy = await getDraftAutopsy(token, id);
  } catch (error) {
    return (
      <div className="py-6">
        <ApiErrorPanel error={error} />
      </div>
    );
  }

  return (
    <div className="space-y-4 py-4">
      <div>
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Draft Autopsy</h1>
        <p className="text-sm text-muted-foreground">
          Every pick graded against the specific player who was still on the board at that
          exact moment — not against how the season turns out. Rank source: {autopsy.rank_source}.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {autopsy.teams.map((team) => (
          <TeamDraftReportCard key={team.team_id} team={team} />
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-base">Full draft board</CardTitle>
          <p className="text-sm text-muted-foreground">
            Every pick, every team. A positive value gap took the better player at that
            position; a negative one reached past someone who graded out better.
          </p>
        </CardHeader>
        <CardContent>
          <DraftBoardGrid teams={autopsy.teams} />
        </CardContent>
      </Card>
    </div>
  );
}
