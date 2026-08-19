import { getPlayoffPlanner } from "@/lib/api";
import { getSessionToken } from "@/lib/auth";
import { ApiErrorPanel } from "@/components/shared/api-error-panel";
import { BracketPanel } from "@/components/playoffs/bracket-panel";
import { SeedingOddsTable } from "@/components/playoffs/seeding-odds-table";
import { TeamPlayoffCard } from "@/components/playoffs/team-playoff-card";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default async function PlayoffPlannerPage({
  params,
}: {
  params: Promise<{ leagueId: string }>;
}) {
  const { leagueId } = await params;
  const id = Number(leagueId);
  const token = (await getSessionToken())!;

  let planner;
  try {
    planner = await getPlayoffPlanner(token, id);
  } catch (error) {
    return (
      <div className="py-6">
        <ApiErrorPanel error={error} />
      </div>
    );
  }

  const seedingByTeamId = new Map(planner.seeding.map((s) => [s.team_id, s]));

  return (
    <div className="space-y-4 py-4">
      <div>
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Playoff Planner</h1>
        <p className="text-sm text-muted-foreground">
          Projected from {planner.n_sims.toLocaleString()} simulated seasons: who makes the{" "}
          {planner.n_playoff_teams}-team bracket, and which position to fix before the
          {" "}
          {planner.n_playoff_rounds}-round playoff window exposes it.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {planner.teams.map((team) => (
          <TeamPlayoffCard
            key={team.team_id}
            team={team}
            seeding={seedingByTeamId.get(team.team_id)}
          />
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-base">Projected bracket</CardTitle>
          <p className="text-sm text-muted-foreground">
            The single most likely seed assignment across every simulated season, paired by the
            league&apos;s own bracket rule (no reseeding between rounds).
          </p>
        </CardHeader>
        <CardContent>
          <BracketPanel bracket={planner.bracket} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-base">Seeding odds</CardTitle>
          <p className="text-sm text-muted-foreground">
            Every team&apos;s chance of landing each seed, or missing the playoffs entirely.
          </p>
        </CardHeader>
        <CardContent>
          <SeedingOddsTable seeding={planner.seeding} />
        </CardContent>
      </Card>
    </div>
  );
}
