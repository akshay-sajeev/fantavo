import { getRoster, getWaiverIntelligence } from "@/lib/api";
import { ApiErrorPanel } from "@/components/shared/api-error-panel";
import { OwnershipNote } from "@/components/waivers/ownership-note";
import { PositionGroupCard } from "@/components/waivers/position-group-card";
import { TeamPicker } from "@/components/waivers/team-picker";

/**
 * Waiver Intelligence: a ranked, position-grouped free-agent priority list
 * for one team -- see sim.api.waiver_intelligence_view's module docstring
 * for the full methodology. Server-rendered per selected team
 * (searchParams.team), same URL-driven pattern this app's other per-team
 * analysis pages (Lineup Optimizer) already use.
 */
export default async function WaiversPage({
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

  const requestedTeamId = team ? Number(team) : undefined;
  const selectedTeamId =
    teams.find((t) => t.team_id === requestedTeamId)?.team_id ?? teams[0].team_id;

  let intel;
  try {
    intel = await getWaiverIntelligence(id, selectedTeamId);
  } catch (error) {
    return (
      <div className="space-y-4 py-4">
        <div>
          <h1 className="font-heading text-2xl font-semibold tracking-tight">
            Waiver Intelligence
          </h1>
        </div>
        <TeamPicker
          leagueId={id}
          teams={teams.map((t) => ({ team_id: t.team_id, team_name: t.team_name }))}
          selectedTeamId={selectedTeamId}
        />
        <ApiErrorPanel error={error} />
      </div>
    );
  }

  return (
    <div className="space-y-4 py-4">
      <div>
        <h1 className="font-heading text-2xl font-semibold tracking-tight">
          Waiver Intelligence
        </h1>
        <p className="text-sm text-muted-foreground">
          Every real free agent in {intel.team_name}&apos;s league, ranked by realistic playing
          time and priced against your own roster&apos;s actual needs -- not a generic top-100.
        </p>
      </div>

      <TeamPicker
        leagueId={id}
        teams={teams.map((t) => ({ team_id: t.team_id, team_name: t.team_name }))}
        selectedTeamId={selectedTeamId}
      />

      <OwnershipNote note={intel.ownership_data_note} />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {intel.groups.map((group) => (
          <PositionGroupCard key={group.position} group={group} />
        ))}
      </div>
    </div>
  );
}
