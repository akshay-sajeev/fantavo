import { getRoster, getSchedule, getSimulation } from "@/lib/api";
import { ApiErrorPanel } from "@/components/shared/api-error-panel";
import { StandingsTable } from "@/components/dashboard/standings-table";
import { CurrentMatchupCard } from "@/components/dashboard/current-matchup-card";
import { RemainingScheduleTable } from "@/components/dashboard/remaining-schedule-table";
import { RostersGrid } from "@/components/dashboard/rosters-grid";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Reveal, RevealItem } from "@/components/ui/motion";

export default async function DashboardPage({
  params,
}: {
  params: Promise<{ leagueId: string }>;
}) {
  const { leagueId } = await params;
  const id = Number(leagueId);

  let simulation, schedule, roster;
  try {
    [simulation, schedule, roster] = await Promise.all([
      getSimulation(id),
      getSchedule(id),
      getRoster(id),
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
      <div>
        <h1 className="font-heading text-2xl font-semibold tracking-tight">League Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          League {simulation.league_id} &middot; {simulation.season_id} season &middot; based on{" "}
          {simulation.n_sims.toLocaleString()} simulated seasons
        </p>
      </div>

      <Reveal className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <RevealItem className="lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle className="font-heading text-base">Standings</CardTitle>
            </CardHeader>
            <CardContent>
              <StandingsTable simulation={simulation} schedule={schedule} />
            </CardContent>
          </Card>
        </RevealItem>
        <RevealItem>
          <CurrentMatchupCard schedule={schedule} />
        </RevealItem>
      </Reveal>

      <Reveal className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <RevealItem className="lg:col-span-2">
          <RostersGrid teams={roster.teams} />
        </RevealItem>
        <RevealItem>
          <RemainingScheduleTable schedule={schedule} />
        </RevealItem>
      </Reveal>
    </div>
  );
}
