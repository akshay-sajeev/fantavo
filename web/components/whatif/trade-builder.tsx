"use client";

import { useMemo, useState } from "react";
import { ArrowLeftRight, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PlayerPicker } from "@/components/whatif/player-picker";
import { TeamSelect } from "@/components/whatif/team-select";
import {
  TradeComparisonResult,
  WhatIfLoadingSkeleton,
} from "@/components/whatif/outcome-comparison";
import type { TeamRoster, WhatIfCompareResponse } from "@/lib/types";

/**
 * Two-team trade builder. Picks are constrained to *equal counts* on both
 * sides -- a full starting-lineup-shape validator (right position counts,
 * right eligibleSlots fit) is out of scope for this phase (see
 * sim/api/app.py's existing whatif validation note), so this keeps the
 * resulting roster the same size instead of risking an invalid lineup.
 * Everything numeric shown after "Run trade" comes straight from the API
 * response -- this component only builds the request and renders it.
 */
export function TradeBuilder({ teams, leagueId }: { teams: TeamRoster[]; leagueId: number }) {
  const [teamAId, setTeamAId] = useState<number>(teams[0]?.team_id);
  const [teamBId, setTeamBId] = useState<number>(teams[1]?.team_id ?? teams[0]?.team_id);
  const [sendFromA, setSendFromA] = useState<number[]>([]);
  const [sendFromB, setSendFromB] = useState<number[]>([]);
  const [state, setState] = useState<
    { status: "idle" } | { status: "loading" } | { status: "error"; message: string } | { status: "done"; result: WhatIfCompareResponse }
  >({ status: "idle" });

  const teamA = teams.find((t) => t.team_id === teamAId);
  const teamB = teams.find((t) => t.team_id === teamBId);

  const countsMatch = sendFromA.length > 0 && sendFromA.length === sendFromB.length;
  const sameTeam = teamAId === teamBId;
  const canRun = countsMatch && !sameTeam && state.status !== "loading";

  function toggleA(playerId: number) {
    setSendFromA((prev) =>
      prev.includes(playerId) ? prev.filter((id) => id !== playerId) : [...prev, playerId],
    );
  }
  function toggleB(playerId: number) {
    setSendFromB((prev) =>
      prev.includes(playerId) ? prev.filter((id) => id !== playerId) : [...prev, playerId],
    );
  }

  function selectTeamA(id: number) {
    setTeamAId(id);
    setSendFromA([]);
    setState({ status: "idle" });
  }
  function selectTeamB(id: number) {
    setTeamBId(id);
    setSendFromB([]);
    setState({ status: "idle" });
  }

  const summary = useMemo(() => {
    if (!teamA || !teamB) return null;
    const aNames = teamA.starters.filter((p) => sendFromA.includes(p.player_id)).map((p) => p.name);
    const bNames = teamB.starters.filter((p) => sendFromB.includes(p.player_id)).map((p) => p.name);
    if (aNames.length === 0 && bNames.length === 0) return null;
    return { aNames, bNames };
  }, [teamA, teamB, sendFromA, sendFromB]);

  async function runTrade() {
    if (!teamA || !teamB || !canRun) return;
    setState({ status: "loading" });

    const newA = [
      ...teamA.starters.filter((p) => !sendFromA.includes(p.player_id)).map((p) => p.player_id),
      ...sendFromB,
    ];
    const newB = [
      ...teamB.starters.filter((p) => !sendFromB.includes(p.player_id)).map((p) => p.player_id),
      ...sendFromA,
    ];

    try {
      const res = await fetch(`/api/league/${leagueId}/whatif-compare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ overrides: { [teamA.team_id]: newA, [teamB.team_id]: newB } }),
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
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="font-heading text-base">
              <TeamSelect
                teams={teams}
                value={teamAId}
                onChange={selectTeamA}
                label="Team sending players (side A)"
                className="w-full"
              />
            </CardTitle>
            <p className="text-xs text-muted-foreground">Select players to send away</p>
          </CardHeader>
          <CardContent>
            <PlayerPicker
              players={teamA?.starters ?? []}
              selected={sendFromA}
              onToggle={toggleA}
              emptyLabel="No starters on this roster."
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="font-heading text-base">
              <TeamSelect
                teams={teams}
                value={teamBId}
                onChange={selectTeamB}
                label="Team sending players (side B)"
                className="w-full"
              />
            </CardTitle>
            <p className="text-xs text-muted-foreground">Select players to send away</p>
          </CardHeader>
          <CardContent>
            <PlayerPicker
              players={teamB?.starters ?? []}
              selected={sendFromB}
              onToggle={toggleB}
              emptyLabel="No starters on this roster."
            />
          </CardContent>
        </Card>
      </div>

      {sameTeam && (
        <p className="flex items-center gap-1.5 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" aria-hidden="true" />
          Pick two different teams to build a trade.
        </p>
      )}
      {!sameTeam && sendFromA.length !== sendFromB.length && (
        <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <AlertTriangle className="h-4 w-4" aria-hidden="true" />
          Both sides need the same number of players ({sendFromA.length} vs {sendFromB.length}
          ) so each roster stays the same size.
        </p>
      )}

      {summary && !sameTeam && (
        <p className="text-sm text-muted-foreground">
          <span className="font-medium text-foreground">{teamA?.team_name}</span> sends{" "}
          {summary.aNames.join(", ") || "nobody"} &middot;{" "}
          <span className="font-medium text-foreground">{teamB?.team_name}</span> sends{" "}
          {summary.bNames.join(", ") || "nobody"}
        </p>
      )}

      <Button onClick={runTrade} disabled={!canRun} className="cursor-pointer">
        <ArrowLeftRight className="h-4 w-4" data-icon="inline-start" aria-hidden="true" />
        Run trade
      </Button>

      {state.status === "loading" && <WhatIfLoadingSkeleton teamCount={2} />}
      {state.status === "error" && (
        <p className="flex items-center gap-1.5 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          {state.message}
        </p>
      )}
      {state.status === "done" && teamA && teamB && (
        <TradeComparisonResult compare={state.result} teamAId={teamA.team_id} teamBId={teamB.team_id} />
      )}
    </div>
  );
}
