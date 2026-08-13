import { getRoster } from "@/lib/api";
import { ApiErrorPanel } from "@/components/shared/api-error-panel";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TradeBuilder } from "@/components/whatif/trade-builder";
import { RosterSwapBuilder } from "@/components/whatif/roster-swap-builder";
import { SeasonReplayPanel } from "@/components/whatif/season-replay-panel";

/**
 * Trade / Roster Swap / Season Replay -- all four PLAN.md what-if types,
 * fed by the existing GET /league/{id}/roster (Trade, Roster Swap) and the
 * new POST .../whatif/season-replay (Alternate Lineup, Schedule
 * Neutrality). Roster data is fetched once here, server-side, and handed to
 * client components that run live scenarios inline (no page navigation per
 * scenario, per PLAN.md).
 */
export default async function WhatIfPage({
  params,
}: {
  params: Promise<{ leagueId: string }>;
}) {
  const { leagueId } = await params;
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

  return (
    <div className="space-y-4 py-4">
      <div>
        <h1 className="font-heading text-2xl font-semibold tracking-tight">What-If</h1>
        <p className="text-sm text-muted-foreground">
          Every scenario below runs the same Monte Carlo simulator that powers the dashboard,
          live at n_sims=2,000, with a different roster or schedule as input.
        </p>
      </div>

      <Tabs defaultValue="trade">
        <TabsList>
          <TabsTrigger value="trade">Trade builder</TabsTrigger>
          <TabsTrigger value="roster-swap">Roster swap</TabsTrigger>
          <TabsTrigger value="season-replay">Season replay</TabsTrigger>
        </TabsList>
        <TabsContent value="trade" className="pt-4">
          <TradeBuilder teams={roster.teams} leagueId={id} />
        </TabsContent>
        <TabsContent value="roster-swap" className="pt-4">
          <RosterSwapBuilder teams={roster.teams} leagueId={id} />
        </TabsContent>
        <TabsContent value="season-replay" className="pt-4">
          <SeasonReplayPanel leagueId={id} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
