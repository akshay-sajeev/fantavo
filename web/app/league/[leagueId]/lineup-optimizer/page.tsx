import { Rocket, Shield, ListChecks } from "lucide-react";
import { getLineupOptimizer, getRoster } from "@/lib/api";
import { getSessionToken } from "@/lib/auth";
import { describeLineupTradeoff } from "@/lib/lineup-optimizer";
import { ApiErrorPanel } from "@/components/shared/api-error-panel";
import { FramingNote } from "@/components/lineup/framing-note";
import { LineupCard } from "@/components/lineup/lineup-card";
import { TeamNavSelect } from "@/components/shared/team-nav-select";
import { TradeoffCallout } from "@/components/lineup/tradeoff-callout";

/**
 * Current / Safest / Highest-upside lineups for one team, side by side --
 * see sim.api.lineup_optimizer_view's module docstring for the full
 * methodology. Server-rendered per selected team (searchParams.team), same
 * URL-driven pattern as this app's other analysis pages.
 */
export default async function LineupOptimizerPage({
  params,
  searchParams,
}: {
  params: Promise<{ leagueId: string }>;
  searchParams: Promise<{ team?: string }>;
}) {
  const { leagueId } = await params;
  const { team } = await searchParams;
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

  const teams = roster.teams;
  if (teams.length === 0) {
    return (
      <div className="py-6">
        <ApiErrorPanel error={new Error("This league has no ingested teams yet.")} />
      </div>
    );
  }

  const requestedTeamId = team ? Number(team) : undefined;
  const selectedTeamId =
    teams.find((t) => t.team_id === requestedTeamId)?.team_id ?? teams[0].team_id;

  let optimizer;
  try {
    optimizer = await getLineupOptimizer(token, id, selectedTeamId);
  } catch (error) {
    return (
      <div className="space-y-4 py-4">
        <div>
          <h1 className="font-heading text-2xl font-semibold tracking-tight">
            Lineup Optimizer
          </h1>
        </div>
        <TeamNavSelect
          leagueId={id}
          path="lineup-optimizer"
          teams={teams.map((t) => ({ team_id: t.team_id, team_name: t.team_name }))}
          selectedTeamId={selectedTeamId}
          label="Choose a team"
        />
        <ApiErrorPanel error={error} />
      </div>
    );
  }

  const tradeoff = describeLineupTradeoff(
    optimizer.current,
    optimizer.safest,
    optimizer.highest_upside,
  );

  const scaleMax = Math.max(
    1,
    optimizer.current.weekly_ceiling,
    optimizer.safest.weekly_ceiling,
    optimizer.highest_upside.weekly_ceiling,
  );

  return (
    <div className="space-y-4 py-4">
      <div>
        <h1 className="font-heading text-2xl font-semibold tracking-tight">
          Lineup Optimizer
        </h1>
        <p className="text-sm text-muted-foreground">
          Three legal lineups for {optimizer.team_name}: your actual lineup, the one with the
          highest projected floor, and the one with the highest simulated title odds.
        </p>
      </div>

      <TeamNavSelect
        leagueId={id}
        path="lineup-optimizer"
        teams={teams.map((t) => ({ team_id: t.team_id, team_name: t.team_name }))}
        selectedTeamId={selectedTeamId}
        label="Choose a team"
      />

      <FramingNote
        nCandidatesConsidered={optimizer.n_candidates_considered}
        weeklyNSims={optimizer.weekly_n_sims}
        seasonNSims={optimizer.season_n_sims}
      />

      <TradeoffCallout tradeoff={tradeoff} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <LineupCard
          projection={optimizer.current}
          icon={ListChecks}
          accentClassName="bg-muted text-foreground"
          subtitle="Your actual ingested starting lineup, unchanged -- the baseline every comparison here is measured against."
          scaleMax={scaleMax}
        />
        <LineupCard
          projection={optimizer.safest}
          icon={Shield}
          accentClassName="bg-secondary/20 text-secondary-foreground"
          subtitle={
            tradeoff.safestFloorDeltaPts > 0.5
              ? `Minimizes a bad week: +${tradeoff.safestFloorDeltaPts.toFixed(1)} pts to your realistic worst-case score, from real simulated samples of your team total.`
              : "Your current lineup already has the best realistic worst-case score among the legal alternatives considered."
          }
          scaleMax={scaleMax}
        />
        <LineupCard
          projection={optimizer.highest_upside}
          icon={Rocket}
          accentClassName="bg-brand-accent/20 text-brand-accent"
          subtitle={
            tradeoff.upsideTitleDeltaPts > 0.3
              ? `Maximizes championship equity: +${tradeoff.upsideTitleDeltaPts.toFixed(1)} pts of simulated title odds versus your current lineup.`
              : "Your current lineup already has the best simulated title odds among the legal alternatives considered."
          }
          scaleMax={scaleMax}
        />
      </div>
    </div>
  );
}
