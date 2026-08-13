import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SeedProbabilityStrip } from "@/components/playoffs/seed-probability-strip";
import { formatPercent } from "@/lib/format";
import type { PlayoffSeedOdds } from "@/lib/types";

/** League-wide seeding odds, sorted by playoff probability -- one shared
 * table below the per-team cards, mirroring Draft Autopsy's "one shared
 * board, once, after every team's own narrative" layout. */
export function SeedingOddsTable({ seeding }: { seeding: PlayoffSeedOdds[] }) {
  const sorted = [...seeding].sort((a, b) => b.playoff_probability - a.playoff_probability);

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Team</TableHead>
            <TableHead className="text-right">Playoff odds</TableHead>
            <TableHead className="text-right">Title odds</TableHead>
            <TableHead>Seed distribution</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((team) => (
            <TableRow key={team.team_id}>
              <TableCell className="font-medium">
                {team.team_name}
                {team.projected_seed !== null && (
                  <span className="ml-1.5 text-xs text-muted-foreground">
                    (projected #{team.projected_seed} seed)
                  </span>
                )}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatPercent(team.playoff_probability, 0)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatPercent(team.title_probability, 0)}
              </TableCell>
              <TableCell className="min-w-[10rem]">
                <SeedProbabilityStrip seedProbabilities={team.seed_probabilities} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
