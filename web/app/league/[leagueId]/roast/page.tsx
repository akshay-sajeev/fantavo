import { getPowerRankingRoast } from "@/lib/api";
import { DraftDataNote } from "@/components/roast/draft-data-note";
import { RoastCard } from "@/components/roast/roast-card";
import { ApiErrorPanel } from "@/components/shared/api-error-panel";

/**
 * Power Ranking Roast (PLAN.md Phase 12, roast half only -- weekly awards
 * are deferred, see docs/decisions.md). Every team, ordered by simulated
 * title probability (the power-ranking order the API already returns),
 * roasted good-naturedly with every joke grounded in a real, already-real
 * fact this codebase already computed elsewhere -- see
 * sim.api.roast_view's module docstring for exactly what's reused from
 * where. No per-team selector: unlike Lineup Optimizer / Waiver
 * Intelligence / Beat My League, this feature is inherently whole-league
 * (everyone gets roasted at once), so there's nothing to pick.
 */
export default async function PowerRankingRoastPage({
  params,
}: {
  params: Promise<{ leagueId: string }>;
}) {
  const { leagueId } = await params;
  const id = Number(leagueId);

  let roast;
  try {
    roast = await getPowerRankingRoast(id);
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
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Power Ranking Roast</h1>
        <p className="text-sm text-muted-foreground">
          Every team, roasted -- good-naturedly, and every line grounded in a real, already-computed
          fact (simulated title odds, real draft picks, real roster gaps), not generic trash talk. Save
          or copy any card to drop it in the group chat.
        </p>
      </div>

      {!roast.has_draft_data ? <DraftDataNote /> : null}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {roast.teams.map((team) => (
          <RoastCard key={team.team_id} team={team} />
        ))}
      </div>
    </div>
  );
}
