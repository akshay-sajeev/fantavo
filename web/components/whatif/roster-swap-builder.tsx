"use client";

import { useState } from "react";
import { AlertTriangle, Repeat } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PlayerPicker } from "@/components/whatif/player-picker";
import {
  SingleTeamComparisonResult,
  WhatIfLoadingSkeleton,
} from "@/components/whatif/outcome-comparison";
import type { TeamRoster, WhatIfCompareResponse } from "@/lib/types";

/**
 * Single-team roster swap: bench one or more current starters, start an
 * equal number of bench players in their place. Same before/after mechanic
 * as the trade builder (sim.engine.simulate_seasons() via the existing
 * POST /league/{id}/whatif, called twice with a shared seed) -- this is the
 * "arbitrary starter override" what-if type from PLAN.md, just scoped to
 * one team instead of two.
 */
export function RosterSwapBuilder({ teams, leagueId }: { teams: TeamRoster[]; leagueId: number }) {
  const [teamId, setTeamId] = useState<number>(teams[0]?.team_id);
  const [bench, setBench] = useState<number[]>([]);
  const [start, setStart] = useState<number[]>([]);
  const [state, setState] = useState<
    { status: "idle" } | { status: "loading" } | { status: "error"; message: string } | { status: "done"; result: WhatIfCompareResponse }
  >({ status: "idle" });

  const team = teams.find((t) => t.team_id === teamId);
  const countsMatch = bench.length > 0 && bench.length === start.length;

  function toggleBench(playerId: number) {
    setBench((prev) => (prev.includes(playerId) ? prev.filter((id) => id !== playerId) : [...prev, playerId]));
  }
  function toggleStart(playerId: number) {
    setStart((prev) => (prev.includes(playerId) ? prev.filter((id) => id !== playerId) : [...prev, playerId]));
  }
  function selectTeam(id: number) {
    setTeamId(id);
    setBench([]);
    setStart([]);
    setState({ status: "idle" });
  }

  async function runSwap() {
    if (!team || !countsMatch) return;
    setState({ status: "loading" });

    const newStarters = [
      ...team.starters.filter((p) => !bench.includes(p.player_id)).map((p) => p.player_id),
      ...start,
    ];

    try {
      const res = await fetch(`/api/league/${leagueId}/whatif-compare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ overrides: { [team.team_id]: newStarters } }),
      });
      const body = await res.json();
      if (!res.ok) {
        throw new Error(body.error ?? `HTTP ${res.status}`);
      }
      setState({ status: "done", result: body as WhatIfCompareResponse });
    } catch (error) {
      setState({
        status: "error",
        message: error instanceof Error ? error.message : "Unknown error",
      });
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-base">
            <select
              value={teamId}
              onChange={(e) => selectTeam(Number(e.target.value))}
              className="w-full max-w-xs cursor-pointer rounded-md border border-border bg-background px-2 py-1.5 text-sm font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
              aria-label="Team to swap"
            >
              {teams.map((t) => (
                <option key={t.team_id} value={t.team_id}>
                  {t.team_name}
                </option>
              ))}
            </select>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <p className="mb-2 text-xs font-medium text-muted-foreground">Bench these starters</p>
              <PlayerPicker
                players={team?.starters ?? []}
                selected={bench}
                onToggle={toggleBench}
                emptyLabel="No starters on this roster."
              />
            </div>
            <div>
              <p className="mb-2 text-xs font-medium text-muted-foreground">Start these bench players</p>
              <PlayerPicker
                players={team?.bench ?? []}
                selected={start}
                onToggle={toggleStart}
                emptyLabel="No bench depth on this roster."
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {bench.length !== start.length && (
        <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <AlertTriangle className="h-4 w-4" aria-hidden="true" />
          Bench and start the same number of players ({bench.length} vs {start.length}) so the
          lineup stays the same size.
        </p>
      )}

      <Button onClick={runSwap} disabled={!countsMatch || state.status === "loading"} className="cursor-pointer">
        <Repeat className="h-4 w-4" data-icon="inline-start" aria-hidden="true" />
        Run roster swap
      </Button>

      {state.status === "loading" && <WhatIfLoadingSkeleton teamCount={1} />}
      {state.status === "error" && (
        <p className="flex items-center gap-1.5 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          {state.message}
        </p>
      )}
      {state.status === "done" && team && (
        <SingleTeamComparisonResult compare={state.result} teamId={team.team_id} />
      )}
    </div>
  );
}
