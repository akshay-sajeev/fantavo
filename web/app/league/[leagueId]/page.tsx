import { getRoster, getSchedule, getSimulation } from "@/lib/api";
import { getSessionToken } from "@/lib/auth";
import { ApiErrorPanel } from "@/components/shared/api-error-panel";
import { StandingsTable } from "@/components/dashboard/standings-table";
import { CurrentMatchupCard } from "@/components/dashboard/current-matchup-card";
import { WeekMatchupsCard } from "@/components/dashboard/week-matchups-card";
import { RostersGrid } from "@/components/dashboard/rosters-grid";
import { RefreshButton } from "@/components/dashboard/refresh-button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Reveal, RevealItem } from "@/components/ui/motion";

export default async function DashboardPage({
  params,
}: {
  params: Promise<{ leagueId: string }>;
}) {
  const { leagueId } = await params;
  const id = Number(leagueId);
  const token = (await getSessionToken())!;

  let simulation, schedule, roster;
  try {
    [simulation, schedule, roster] = await Promise.all([
      getSimulation(token, id),
      getSchedule(token, id),
      getRoster(token, id),
    ]);
  } catch (error) {
    return (
      <div className="py-6">
        <ApiErrorPanel error={error} />
      </div>
    );
  }

  return (
    <div className="space-y-4 py-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-heading text-2xl font-semibold tracking-tight">League Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            League {simulation.league_id} &middot; {simulation.season_id} season &middot; based on{" "}
            {simulation.n_sims.toLocaleString()} simulated seasons
          </p>
        </div>
        <RefreshButton leagueId={id} />
      </div>

      {/* Grid items stretch by default, so the stacked pair in column 3 is
          exactly as tall as the Standings card beside it, and the two split
          that height evenly. Below lg everything collapses to one column at
          natural height -- forcing equal heights on a narrow screen would
          only manufacture scroll boxes. */}
      <Reveal className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <RevealItem className="lg:col-span-2">
          <Card className="h-full">
            <CardHeader>
              <CardTitle className="font-heading text-base">Standings</CardTitle>
            </CardHeader>
            <CardContent>
              <StandingsTable simulation={simulation} schedule={schedule} />
            </CardContent>
          </Card>
        </RevealItem>
        {/* The two wrappers carry the flex sizing so both children are
            structurally identical flex items -- putting flex-1 directly on
            the cards splits the height unevenly, because one is wrapped in
            TiltCard and the other is not. The cards just fill their box. */}
        <RevealItem className="flex flex-col gap-4">
          <div className="min-h-0 flex-1">
            <CurrentMatchupCard schedule={schedule} className="h-full" />
          </div>
          <div className="min-h-0 flex-1">
            <WeekMatchupsCard schedule={schedule} className="h-full" />
          </div>
        </RevealItem>
      </Reveal>

      <Reveal className="grid grid-cols-1 gap-4">
        <RevealItem>
          <RostersGrid teams={roster.teams} />
        </RevealItem>
      </Reveal>
    </div>
  );
}
