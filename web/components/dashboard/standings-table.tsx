"use client";

import { AlertTriangle, Info } from "lucide-react";
import { motion, useReducedMotion } from "framer-motion";
import {
  Table,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { AnimatedMeter, revealContainerVariants, revealItemVariants } from "@/components/ui/motion";
import { formatPercent, formatWins } from "@/lib/format";
import { tallyActualRecords, totalGamesPlayed } from "@/lib/standings";
import { cn } from "@/lib/utils";
import type { ScheduleResponse, SimulationResponse } from "@/lib/types";

/**
 * "Lead with analysis, not a copy of ESPN's UI" + "where roster strength and
 * record disagree, say so" (PLAN.md). Simulated strength is ranked by
 * `mean_wins` from the simulation endpoint (never a frontend-computed
 * score). Actual record is tallied from the schedule endpoint's own
 * per-matchup `winner` field (see lib/standings.ts) -- literal counting of
 * already-decided results, not a projection.
 *
 * When zero games have been played league-wide (true for this pre-season /
 * synthetic league today), there is nothing to compare a projection
 * against, so this renders an explicit "no games played yet" note instead
 * of a fabricated disagreement -- never invents a game result. Once real
 * weekly results exist, the "Diverges from projection" flag activates
 * automatically, on the same code path, with no special-casing.
 */
export function StandingsTable({
  simulation,
  schedule,
}: {
  simulation: SimulationResponse;
  schedule: ScheduleResponse;
}) {
  const reduceMotion = useReducedMotion();
  const bySimStrength = [...simulation.teams].sort((a, b) => b.mean_wins - a.mean_wins);
  const actualRecords = tallyActualRecords(schedule);
  const gamesPlayed = totalGamesPlayed(actualRecords);

  const actualRank = new Map<number, number>();
  if (gamesPlayed > 0) {
    const byActualWins = [...bySimStrength].sort((a, b) => {
      const aw = actualRecords.get(a.team_id)?.wins ?? 0;
      const bw = actualRecords.get(b.team_id)?.wins ?? 0;
      return bw - aw;
    });
    byActualWins.forEach((t, i) => actualRank.set(t.team_id, i));
  }

  return (
    <div className="space-y-3">
      {gamesPlayed === 0 ? (
        <div className="flex items-start gap-2 rounded-lg border border-border bg-muted/60 p-3 text-sm text-muted-foreground">
          <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <p>
            No games have been played yet this season
            {schedule.current_week ? ` — Week ${schedule.current_week} is upcoming` : ""}. The
            standings below are simulated projections ({simulation.n_sims.toLocaleString()}{" "}
            simulated seasons), not an actual win-loss record.
          </p>
        </div>
      ) : null}

      <div className="overflow-x-auto rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">#</TableHead>
              <TableHead>Team</TableHead>
              <TableHead className="text-right">
                {gamesPlayed > 0 ? "Actual record" : "Projected record"}
              </TableHead>
              <TableHead className="text-right">Playoff %</TableHead>
              <TableHead className="text-right">Title %</TableHead>
              {gamesPlayed > 0 && <TableHead className="text-right">Note</TableHead>}
            </TableRow>
          </TableHeader>
          <motion.tbody
            data-slot="table-body"
            className="[&_tr:last-child]:border-0"
            variants={reduceMotion ? undefined : revealContainerVariants}
            initial={reduceMotion ? undefined : "hidden"}
            animate={reduceMotion ? undefined : "show"}
          >
            {bySimStrength.map((team, projectedRank) => {
              const record = actualRecords.get(team.team_id);
              const diverges =
                gamesPlayed > 0 &&
                Math.abs((actualRank.get(team.team_id) ?? projectedRank) - projectedRank) >= 3;
              const isTop = projectedRank === 0;
              return (
                <motion.tr
                  key={team.team_id}
                  variants={reduceMotion ? undefined : revealItemVariants}
                  className={cn(
                    "border-b border-border/70 transition-all duration-150 hover:bg-primary/5 hover:shadow-[inset_2px_0_0_0_var(--color-primary)] data-[state=selected]:bg-muted",
                    isTop && "bg-brand-accent/5 shadow-[inset_2px_0_0_0_var(--color-brand-accent)]"
                  )}
                >
                  <TableCell className="tabular-nums text-muted-foreground">
                    {projectedRank + 1}
                  </TableCell>
                  <TableCell className="font-medium">{team.team_name}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {gamesPlayed > 0 && record
                      ? `${record.wins}-${record.losses}${record.ties ? `-${record.ties}` : ""}`
                      : `${formatWins(team.mean_wins)} proj. wins`}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex flex-col items-end gap-1">
                      <span className="tabular-nums">{formatPercent(team.playoff_probability)}</span>
                      <AnimatedMeter
                        value={team.playoff_probability}
                        label={`${team.team_name} playoff probability`}
                      />
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex flex-col items-end gap-1">
                      <span className="tabular-nums">{formatPercent(team.title_probability)}</span>
                      <AnimatedMeter
                        value={team.title_probability}
                        label={`${team.team_name} title probability`}
                        glow="accent"
                      />
                    </div>
                  </TableCell>
                  {gamesPlayed > 0 && (
                    <TableCell className="text-right">
                      {diverges ? (
                        <span className="inline-flex items-center gap-1 text-xs font-medium text-destructive">
                          <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                          Record diverges from projected strength
                        </span>
                      ) : null}
                    </TableCell>
                  )}
                </motion.tr>
              );
            })}
          </motion.tbody>
        </Table>
      </div>
    </div>
  );
}
