import { AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { RangeBar } from "@/components/risk/range-bar";
import { formatPercent } from "@/lib/format";
import type { RosterPlayer, TeamRoster } from "@/lib/types";

/** Starters are guaranteed to carry a real projection by the API contract
 * (sim/api/roster_view.py hard-refuses to build a team with an unprojected
 * starter) -- this narrows the nullable RosterPlayer fields to plain
 * numbers for exactly that population, failing loudly rather than silently
 * rendering a zero'd bar if that contract is ever violated. */
function projectedStarter(
  player: RosterPlayer,
): RosterPlayer & { mean: number; sd: number; availability: number; floor: number; ceiling: number } {
  if (player.mean === null || player.floor === null || player.ceiling === null) {
    throw new Error(
      `starter ${player.player_id} (${player.name}) has no projection -- the API should never return this`,
    );
  }
  return player as RosterPlayer & {
    mean: number;
    sd: number;
    availability: number;
    floor: number;
    ceiling: number;
  };
}

/** Presentation-only banding of the already-computed `risk_rating` number
 * into low/medium/high -- a display threshold, not a new statistic. */
function riskBand(riskRating: number): { label: string; className: string } {
  if (riskRating >= 0.15) {
    return { label: "High risk", className: "bg-destructive/15 text-destructive border-destructive/30" };
  }
  if (riskRating >= 0.05) {
    return {
      label: "Moderate risk",
      className: "bg-brand-accent/15 text-brand-accent border-brand-accent/40",
    };
  }
  return { label: "Low risk", className: "bg-primary/10 text-primary border-primary/25" };
}

export function TeamRiskCard({ team }: { team: TeamRoster }) {
  const band = riskBand(team.risk_rating);
  const starters = team.starters.map(projectedStarter);
  const scaleMax = Math.max(1, ...starters.map((p) => p.ceiling));

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div>
          <CardTitle className="font-heading text-base">{team.team_name}</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            {formatPercent(team.risk_rating)} of expected starting points is exposed to
            weekly unavailability
          </p>
        </div>
        <Badge variant="outline" className={band.className}>
          {band.label}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        {team.positional_concentration.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {team.positional_concentration.map((flag) => (
              <span
                key={flag}
                className="inline-flex items-center gap-1 rounded-md border border-destructive/25 bg-destructive/5 px-2 py-1 text-xs text-destructive"
              >
                <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                {flag}
              </span>
            ))}
          </div>
        )}

        {starters.length === 0 ? (
          <p className="text-sm text-muted-foreground">No roster ingested for this team yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Player</TableHead>
                  <TableHead>Pos</TableHead>
                  <TableHead>Slot</TableHead>
                  <TableHead className="text-right">Availability</TableHead>
                  <TableHead className="text-right">Floor–Ceiling (pts/gm)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {starters.map((player) => (
                  <TableRow key={player.player_id}>
                    <TableCell className="font-medium">{player.name}</TableCell>
                    <TableCell className="text-muted-foreground">{player.position}</TableCell>
                    <TableCell className="text-muted-foreground">{player.lineup_slot}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatPercent(player.availability, 0)}
                    </TableCell>
                    <TableCell>
                      <RangeBar
                        floor={player.floor}
                        mean={player.mean}
                        ceiling={player.ceiling}
                        scaleMax={scaleMax}
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
