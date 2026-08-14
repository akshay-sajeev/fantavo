"use client";

import { useState } from "react";
import { AlertTriangle, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatWins } from "@/lib/format";
import type { SeasonReplayResponse } from "@/lib/types";

function record(wins: number, losses: number, ties: number): string {
  return ties > 0 ? `${wins}-${losses}-${ties}` : `${wins}-${losses}`;
}

/**
 * Alternate-lineup (actual vs. optimal starting lineup, replayed week by
 * week) and schedule-neutrality (expected record if every team played
 * every team every week) -- both computed server-side by
 * sim.api.season_replay_view over ONE sampled "actual" season, since no
 * real weekly scores exist yet for this league (see that module's
 * docstring and docs/decisions.md Phase 6). The SYNTHETIC banner is not
 * optional decoration -- it is required labeling for exactly the kind of
 * fabricated-but-real-model-derived data CLAUDE.md's "no invented numbers"
 * rule cares about, so it stays visible everywhere this data renders.
 */
export function SeasonReplayPanel({ leagueId }: { leagueId: number }) {
  const [state, setState] = useState<
    | { status: "idle" }
    | { status: "loading" }
    | { status: "error"; message: string }
    | { status: "done"; result: SeasonReplayResponse }
  >({ status: "idle" });

  async function runReplay() {
    setState({ status: "loading" });
    try {
      const res = await fetch(`/api/league/${leagueId}/season-replay`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const body = await res.json();
      if (!res.ok) {
        throw new Error(body.error ?? `HTTP ${res.status}`);
      }
      setState({ status: "done", result: body as SeasonReplayResponse });
    } catch (error) {
      setState({
        status: "error",
        message: error instanceof Error ? error.message : "Unknown error",
      });
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Replays one sampled season against the actual starting lineup vs. the best possible
        lineup each week, and against a fully schedule-neutral slate (every team plays every
        other team every week).
      </p>

      <Button onClick={runReplay} disabled={state.status === "loading"} className="cursor-pointer">
        <Play className="h-4 w-4" data-icon="inline-start" aria-hidden="true" />
        Run season replay
      </Button>

      {state.status === "loading" && (
        <div className="space-y-2" aria-live="polite" aria-busy="true">
          <Skeleton className="h-16 w-full rounded-lg" />
          <Skeleton className="h-64 w-full rounded-lg" />
        </div>
      )}

      {state.status === "error" && (
        <p className="flex items-center gap-1.5 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          {state.message}
        </p>
      )}

      {state.status === "done" && (
        <div className="space-y-3">
          <div className="flex items-start gap-3 rounded-lg border border-brand-accent/40 bg-brand-accent/10 p-4 text-sm">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-brand-accent" aria-hidden="true" />
            <div>
              <p className="font-medium text-foreground">{state.result.note}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Seed {state.result.seed} &middot; {state.result.n_regular_weeks} weeks
              </p>
            </div>
          </div>

          <div className="overflow-x-auto rounded-lg border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Team</TableHead>
                  <TableHead className="text-right">Actual record</TableHead>
                  <TableHead className="text-right">Optimal-lineup record</TableHead>
                  <TableHead className="text-right">Points left on bench</TableHead>
                  <TableHead className="text-right">Schedule-neutral record</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {state.result.teams.map((team) => {
                  const pointsLeftOnBench = team.optimal_points_for - team.actual_points_for;
                  return (
                    <TableRow key={team.team_id}>
                      <TableCell className="font-medium">{team.team_name}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {record(team.actual_wins, team.actual_losses, team.actual_ties)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {record(team.optimal_wins, team.optimal_losses, team.optimal_ties)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {pointsLeftOnBench > 0.05 ? `+${pointsLeftOnBench.toFixed(1)}` : "0.0"}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatWins(team.neutral_expected_wins)}-
                        {formatWins(team.neutral_expected_losses)}
                        {team.neutral_expected_ties > 0.05
                          ? `-${formatWins(team.neutral_expected_ties)}`
                          : ""}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </div>
  );
}
