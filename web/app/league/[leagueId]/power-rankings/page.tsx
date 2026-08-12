import { getSimulation } from "@/lib/api";
import { ApiErrorPanel } from "@/components/shared/api-error-panel";
import { RankingsBarChart } from "@/components/power-rankings/rankings-bar-chart";
import { RankingsTable } from "@/components/power-rankings/rankings-table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default async function PowerRankingsPage({
  params,
}: {
  params: Promise<{ leagueId: string }>;
}) {
  const { leagueId } = await params;
  const id = Number(leagueId);

  let simulation;
  try {
    simulation = await getSimulation(id);
  } catch (error) {
    return (
      <div className="py-6">
        <ApiErrorPanel error={error} />
      </div>
    );
  }

  // Sort by the API's own title_probability -- the only ordering PLAN.md
  // permits ("NOT a computed score"). Ties broken by playoff_probability,
  // also an API field, purely for stable display order.
  const teams = [...simulation.teams].sort(
    (a, b) => b.title_probability - a.title_probability || b.playoff_probability - a.playoff_probability,
  );

  return (
    <div className="space-y-4 py-4">
      <div>
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Power Rankings</h1>
        <p className="text-sm text-muted-foreground">
          Ordered by simulated championship probability across{" "}
          {simulation.n_sims.toLocaleString()} simulated seasons — not a hand-weighted score.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-base">Championship probability</CardTitle>
        </CardHeader>
        <CardContent>
          <RankingsBarChart teams={teams} />
        </CardContent>
      </Card>

      <div>
        <h2 className="mb-2 font-heading text-lg font-semibold">Full breakdown</h2>
        <p className="mb-3 text-sm text-muted-foreground">
          A title chance alone doesn&apos;t say whether a team&apos;s outcomes are bunched
          tightly around that number or spread across a wide range of finishes — the finish
          distribution shows the shape.
        </p>
        <RankingsTable teams={teams} />
      </div>
    </div>
  );
}
