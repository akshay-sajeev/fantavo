import { getRoster } from "@/lib/api";
import { getSessionToken } from "@/lib/auth";
import { ApiErrorPanel } from "@/components/shared/api-error-panel";
import { TeamNavSelect } from "@/components/shared/team-nav-select";
import { AnalystChat } from "@/components/analyst/chat";

/**
 * AI League Analyst: a real chat interface over sim.api.analyst_view's
 * Gemini tool-calling loop -- see that module and sim.api.analyst_tools for
 * the full methodology. Server-rendered per selected team
 * (searchParams.team), the same URL-driven per-team pattern this app's
 * other per-team analysis pages (Lineup Optimizer, Waiver Intelligence,
 * Beat My League) already use -- there is no auth/league-picker in this
 * app, so a team must be chosen the same way every time.
 */
export default async function AnalystPage({
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
  const selected = teams.find((t) => t.team_id === requestedTeamId) ?? teams[0];
  const rivalTeamName = teams.find((t) => t.team_id !== selected.team_id)?.team_name ?? null;

  return (
    <div className="space-y-4 py-4">
      <div>
        <h1 className="font-heading text-2xl font-semibold tracking-tight">AI League Analyst</h1>
        <p className="text-sm text-muted-foreground">
          Ask real questions about {selected.team_name}. The analyst calls this league&apos;s real
          simulation and roster-analysis tools and narrates the results -- it never estimates or
          invents a number.
        </p>
      </div>

      <TeamNavSelect
        leagueId={id}
        path="analyst"
        teams={teams.map((t) => ({ team_id: t.team_id, team_name: t.team_name }))}
        selectedTeamId={selected.team_id}
        label="Choose a team"
      />

      <AnalystChat
        key={selected.team_id}
        leagueId={id}
        teamId={selected.team_id}
        teamName={selected.team_name}
        rivalTeamName={rivalTeamName}
      />
    </div>
  );
}
