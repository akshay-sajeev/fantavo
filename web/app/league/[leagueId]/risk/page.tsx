import { getRoster } from "@/lib/api";
import { getSessionToken } from "@/lib/auth";
import { ApiErrorPanel } from "@/components/shared/api-error-panel";
import { TeamRiskCard } from "@/components/risk/team-risk-card";

export default async function RiskPage({
  params,
}: {
  params: Promise<{ leagueId: string }>;
}) {
  const { leagueId } = await params;
  const id = Number(leagueId);
  const token = (await getSessionToken())!;

  let roster;
  try {
    roster = await getRoster(token, id);
  } catch (error) {
    return (
      <div className="py-6">
        <ApiErrorPanel error={error} />
      </div>
    );
  }

  // Highest risk_rating first -- both risk_rating and positional_concentration
  // are computed server-side (sim/api/roster_view.py), never re-derived here.
  const teams = [...roster.teams].sort((a, b) => b.risk_rating - a.risk_rating);

  return (
    <div className="space-y-4 py-4">
      <div>
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Roster Risk</h1>
        <p className="text-sm text-muted-foreground">
          Per-player availability, floor and ceiling, rolled up into a team risk rating and
          positional concentration flags.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {teams.map((team) => (
          <TeamRiskCard key={team.team_id} team={team} />
        ))}
      </div>
    </div>
  );
}
