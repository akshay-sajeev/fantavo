"use client";

import { Flame, Trophy } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { FinishDistributionStrip } from "@/components/shared/finish-distribution-strip";
import { RoastFacts } from "@/components/roast/roast-facts";
import { formatPercent, formatWins } from "@/lib/format";
import { teamColor } from "@/lib/chart-colors";
import type { TeamOutcome, TeamRoast } from "@/lib/types";

/**
 * Full detail behind each power-ranking bar: playoff odds and the finish
 * distribution shape alongside the title-probability number, per PLAN.md
 * ("Show playoff odds and finish distribution alongside each team") and
 * CLAUDE.md ("a 21% title chance with a wide finish distribution means
 * something different from 21% with a narrow one"). `teams` is rendered in
 * the order given -- the caller sorts by the API's own title_probability.
 *
 * Client component because each row opens that team's roast in a Dialog
 * (formerly its own page, see docs/decisions.md Phase 12) -- `roastByTeamId`
 * is a lookup rather than a second fetch, since the caller already has the
 * full roast response server-side.
 */
export function RankingsTable({
  teams,
  roastByTeamId,
}: {
  teams: TeamOutcome[];
  roastByTeamId: Map<number, TeamRoast>;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-10">#</TableHead>
            <TableHead>Team</TableHead>
            <TableHead className="text-right">Title %</TableHead>
            <TableHead className="text-right">Playoffs %</TableHead>
            <TableHead className="text-right">Proj. wins</TableHead>
            <TableHead className="min-w-[10rem]">Finish distribution</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {teams.map((team, index) => {
            const roast = roastByTeamId.get(team.team_id);
            return (
              <Dialog key={team.team_id}>
                <DialogTrigger
                  nativeButton={false}
                  render={
                    <TableRow
                      className="cursor-pointer transition-colors hover:bg-muted/40"
                      aria-label={`${team.team_name} roast`}
                    />
                  }
                >
                  <TableCell className="tabular-nums text-muted-foreground">{index + 1}</TableCell>
                  <TableCell className="font-medium">
                    <span className="inline-flex items-center gap-2">
                      <span
                        className="h-2.5 w-2.5 shrink-0 rounded-full"
                        style={{ backgroundColor: teamColor(index) }}
                        aria-hidden="true"
                      />
                      {team.team_name}
                    </span>
                  </TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">
                    {formatPercent(team.title_probability)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatPercent(team.playoff_probability)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatWins(team.mean_wins)}
                  </TableCell>
                  <TableCell>
                    <FinishDistributionStrip finishDistribution={team.finish_distribution} />
                  </TableCell>
                </DialogTrigger>
                {roast ? <TeamRoastDialogContent roast={roast} /> : null}
              </Dialog>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function TeamRoastDialogContent({ roast }: { roast: TeamRoast }) {
  const isTop = roast.title_rank === 1;
  const isBottom = roast.title_rank === roast.league_team_count;

  return (
    <DialogContent>
      <div className="flex items-start justify-between gap-3 pr-8">
        <div>
          <DialogTitle>{roast.team_name}</DialogTitle>
          <p className="text-xs text-muted-foreground">
            {roast.title_rank} of {roast.league_team_count} in the power rankings &middot;{" "}
            {formatPercent(roast.title_probability, 1)} simulated title odds
          </p>
        </div>
        {isTop || isBottom ? (
          <span
            className={
              isTop
                ? "flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-accent/15 text-brand-accent"
                : "flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive"
            }
            aria-hidden="true"
          >
            {isTop ? <Trophy className="h-4 w-4" /> : <Flame className="h-4 w-4" />}
          </span>
        ) : null}
      </div>

      <p className="text-sm leading-relaxed text-foreground/90">{roast.roast}</p>

      <RoastFacts facts={roast.facts} />
    </DialogContent>
  );
}
