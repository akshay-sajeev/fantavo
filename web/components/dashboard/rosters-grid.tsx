import { ChevronDown } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatPoints } from "@/lib/format";
import type { RosterPlayer, TeamRoster } from "@/lib/types";

/**
 * Compact per-team roster listing for the dashboard overview. Star Player
 * (highest-projected starter) and Proj. Points (sum of starters' mean
 * projections) are simple selection/summation over per-player numbers the
 * roster API already returns -- never a new modeled number, and never a
 * "Roster Grade" (no such rating exists anywhere in this app's data model).
 * Availability/floor/ceiling/risk still live on the dedicated Risk panel
 * (/risk), not duplicated here.
 *
 * <summary> must be the <details> element's first child for correct native
 * disclosure semantics, so the always-visible team name/stats live in a
 * plain wrapper alongside (not inside) the <details> -- only the full
 * starters list is native-collapsible. Keyboard- and screen-reader-
 * accessible with no client JS needed, which matters since this whole page
 * is a Server Component.
 */
export function RostersGrid({ teams }: { teams: TeamRoster[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-base">Rosters</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {teams.map((team) => {
            const eligible = team.starters.filter(
              (p): p is RosterPlayer & { mean: number } => p.mean != null
            );
            const starPlayer =
              eligible.length > 0
                ? eligible.reduce((best, p) => (p.mean > best.mean ? p : best))
                : null;
            const projectedPoints = eligible.reduce((sum, p) => sum + p.mean, 0);

            return (
              <div
                key={team.team_id}
                className="rounded-lg border border-border/70 p-3 transition-all duration-150 hover:bg-primary/5 hover:shadow-[var(--shadow-glow-primary)]"
              >
                <div className="space-y-2">
                  <p className="font-medium">{team.team_name}</p>
                  {starPlayer ? (
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">Star Player</span>
                      <span className="font-medium">
                        {starPlayer.name}{" "}
                        <span className="text-xs text-muted-foreground">
                          {starPlayer.position}
                        </span>
                      </span>
                    </div>
                  ) : null}
                  {eligible.length > 0 ? (
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">Proj. Points</span>
                      <span className="font-medium tabular-nums">
                        {formatPoints(projectedPoints)}
                      </span>
                    </div>
                  ) : null}
                </div>

                <details className="group mt-2">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-2 rounded-md border border-border/70 px-2 py-1 text-xs font-medium text-muted-foreground marker:hidden hover:bg-muted/60 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring">
                    View Full Roster
                    <ChevronDown
                      className="h-3.5 w-3.5 shrink-0 transition-transform duration-150 group-open:rotate-180"
                      aria-hidden="true"
                    />
                  </summary>
                  <ul className="mt-2 space-y-1 text-sm">
                    {team.starters.length === 0 ? (
                      <li className="text-muted-foreground">No roster ingested yet.</li>
                    ) : (
                      team.starters.map((p) => (
                        <li key={p.player_id} className="flex items-center justify-between gap-2">
                          <span>{p.name}</span>
                          <span className="shrink-0 text-xs text-muted-foreground">
                            {p.lineup_slot}
                          </span>
                        </li>
                      ))
                    )}
                  </ul>
                </details>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
