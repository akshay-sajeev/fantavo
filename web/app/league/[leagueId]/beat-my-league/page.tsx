import { getBeatMyLeague, getRoster } from "@/lib/api";
import { AdvantageCard } from "@/components/beat-my-league/advantage-card";
import { LeagueComparisonTable } from "@/components/beat-my-league/league-comparison-table";
import { ThreatCard } from "@/components/beat-my-league/threat-card";
import { TeamNavSelect } from "@/components/shared/team-nav-select";
import { TradeCautionList } from "@/components/beat-my-league/trade-caution-list";
import { ApiErrorPanel } from "@/components/shared/api-error-panel";
import { getLeagueConnection } from "@/lib/leagueConnection";

/**
 * Beat My League: for every team, title odds / structural strengths &
 * weaknesses / playoff schedule difficulty (all reused from Playoff
 * Planner's own already-computed output); then, for the selected team, the
 * single biggest threat and why, where the user holds a real advantage, and
 * which positions not to trade away -- see sim.api.beat_my_league_view's
 * module docstring for the full methodology. Server-rendered per selected
 * team (searchParams.team), the same URL-driven pattern this app's other
 * per-team analysis pages (Lineup Optimizer, Waiver Intelligence) already
 * use.
 *
 * Auth Phase B: this is the one page where the connected user's own picked
 * team (`getLeagueConnection().team_id`) is used as the *default*
 * selection -- this page's whole framing is "your team versus the rest of
 * the league" (note its "Choose your team" label, unlike the other pages'
 * "Choose a team"), so defaulting to an arbitrary teams[0] was the least
 * defensible here. An explicit `?team=` still wins; the connected team is
 * only the fallback, replacing what used to be teams[0].
 */
export default async function BeatMyLeaguePage({
  params,
  searchParams,
}: {
  params: Promise<{ leagueId: string }>;
  searchParams: Promise<{ team?: string }>;
}) {
  const { leagueId } = await params;
  const { team } = await searchParams;
  const id = Number(leagueId);

  let roster;
  try {
    roster = await getRoster(id);
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

  // Precedence: explicit ?team= > the user's own connected team > teams[0].
  // teams[0] stays as the last resort for the case where the connected team
  // isn't in this roster payload.
  const connection = await getLeagueConnection();
  const requestedTeamId = team ? Number(team) : (connection?.team_id ?? undefined);
  const selectedTeamId =
    teams.find((t) => t.team_id === requestedTeamId)?.team_id ?? teams[0].team_id;

  let analysis;
  try {
    analysis = await getBeatMyLeague(id, selectedTeamId);
  } catch (error) {
    return (
      <div className="space-y-4 py-4">
        <div>
          <h1 className="font-heading text-2xl font-semibold tracking-tight">Beat My League</h1>
        </div>
        <TeamNavSelect
          leagueId={id}
          path="beat-my-league"
          teams={teams.map((t) => ({ team_id: t.team_id, team_name: t.team_name }))}
          selectedTeamId={selectedTeamId}
          label="Choose your team"
        />
        <ApiErrorPanel error={error} />
      </div>
    );
  }

  return (
    <div className="space-y-4 py-4">
      <div>
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Beat My League</h1>
        <p className="text-sm text-muted-foreground">
          {analysis.team_name}&apos;s biggest threat, real advantage, and trade strategy --
          specific to this league&apos;s actual rosters, not generic fantasy advice.
        </p>
      </div>

      <TeamNavSelect
        leagueId={id}
        path="beat-my-league"
        teams={teams.map((t) => ({ team_id: t.team_id, team_name: t.team_name }))}
        selectedTeamId={selectedTeamId}
        label="Choose your team"
      />

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <ThreatCard threat={analysis.biggest_threat} />
        <AdvantageCard advantage={analysis.real_advantage} />
      </div>

      <TradeCautionList cautions={analysis.trade_cautions} />

      <div>
        <p className="mb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          Every team in the league
        </p>
        <LeagueComparisonTable
          teams={analysis.teams}
          selectedTeamId={selectedTeamId}
          threatTeamId={analysis.biggest_threat.team_id}
        />
      </div>
    </div>
  );
}
