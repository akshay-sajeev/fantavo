import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { formatPercent, ordinal } from "@/lib/format";
import type { TeamLeagueProfile } from "@/lib/types";

/**
 * Every team, side by side -- the Comparative Analysis Dashboard pattern
 * design-system/MASTER.md points to, and PLAN.md's own "for every team:
 * title probability, structural strengths, weaknesses, playoff schedule
 * difficulty" requirement. A real shadcn `Table` (per the ui-ux-pro-max
 * stack guidance for shadcn: Table for tabular data, not a div grid), not a
 * repeat of the threat/advantage cards above -- those two are the ONE
 * head-to-head comparison that matters for the selected team; this table is
 * the full-league context around it. The selected team and the identified
 * threat are both highlighted so their row in the wider comparison is easy
 * to find.
 */
export function LeagueComparisonTable({
  teams,
  selectedTeamId,
  threatTeamId,
}: {
  teams: TeamLeagueProfile[];
  selectedTeamId: number;
  threatTeamId: number;
}) {
  const sorted = [...teams].sort((a, b) => b.title_probability - a.title_probability);

  return (
    <div className="overflow-x-auto rounded-lg ring-1 ring-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Team</TableHead>
            <TableHead className="text-right">Title odds</TableHead>
            <TableHead>Structural strengths</TableHead>
            <TableHead>Structural weakness</TableHead>
            <TableHead>Playoff schedule difficulty</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((team) => {
            const isUser = team.team_id === selectedTeamId;
            const isThreat = team.team_id === threatTeamId;
            return (
              <TableRow
                key={team.team_id}
                className={cn(isUser && "bg-primary/5", isThreat && !isUser && "bg-destructive/5")}
              >
                <TableCell className="font-medium whitespace-nowrap">
                  <span className="flex items-center gap-1.5">
                    {team.team_name}
                    {isUser && (
                      <Badge variant="default" className="text-[10px]">
                        You
                      </Badge>
                    )}
                    {isThreat && !isUser && (
                      <Badge variant="destructive" className="text-[10px]">
                        Threat
                      </Badge>
                    )}
                  </span>
                </TableCell>
                <TableCell className="text-right font-semibold tabular-nums">
                  {formatPercent(team.title_probability, 1)}
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {team.strengths.length > 0 ? (
                      team.strengths.map((s) => (
                        <Badge key={s.slot_label} variant="outline" className="text-[10px]">
                          {s.slot_label} ({ordinal(Math.round(s.league_percentile) - 1)} pct)
                        </Badge>
                      ))
                    ) : (
                      <span className="text-xs text-muted-foreground">None</span>
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  {team.weaknesses.length > 0 ? (
                    <Badge variant="destructive" className="text-[10px]">
                      {team.weaknesses[0].slot_label} (rank {team.weaknesses[0].league_rank}/
                      {team.weaknesses[0].league_team_count})
                    </Badge>
                  ) : (
                    <span className="text-xs text-muted-foreground">None flagged</span>
                  )}
                </TableCell>
                <TableCell className="min-w-[22rem] text-xs leading-relaxed text-muted-foreground">
                  {team.playoff_schedule_note}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
