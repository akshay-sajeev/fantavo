import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { FinishDistributionStrip } from "@/components/shared/finish-distribution-strip";
import { formatPercent, formatWins } from "@/lib/format";
import { teamColor } from "@/lib/chart-colors";
import type { TeamOutcome } from "@/lib/types";

/**
 * Full detail behind each power-ranking bar: playoff odds and the finish
 * distribution shape alongside the title-probability number, per PLAN.md
 * ("Show playoff odds and finish distribution alongside each team") and
 * CLAUDE.md ("a 21% title chance with a wide finish distribution means
 * something different from 21% with a narrow one"). `teams` is rendered in
 * the order given -- the caller sorts by the API's own title_probability.
 */
export function RankingsTable({ teams }: { teams: TeamOutcome[] }) {
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
          {teams.map((team, index) => (
            <TableRow key={team.team_id}>
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
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
