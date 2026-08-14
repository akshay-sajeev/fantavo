import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TeamRosterDialog } from "@/components/dashboard/team-roster-dialog";
import { formatPoints } from "@/lib/format";
import type { RosterPlayer, TeamRoster } from "@/lib/types";

/**
 * Compact per-team roster listing for the dashboard overview. Star Player
 * (highest-projected starter) and Proj. Pts/Wk (sum of starters' per-game
 * mean projections -- a one-week total, not a season total) are simple
 * selection/summation over per-player numbers the roster API already
 * returns -- never a new modeled number, and never a "Roster Grade" (no
 * such rating exists anywhere in this app's data model).
 * Availability/floor/ceiling/risk still live on the dedicated Risk panel
 * (/risk), not duplicated here.
 *
 * The full roster (starters AND bench) opens in a modal rather than
 * expanding inline -- there is no room for both lists in a card this size.
 * TeamRosterDialog is uncontrolled, so this stays a Server Component.
 */
export function RostersGrid({ teams }: { teams: TeamRoster[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-base">Rosters</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
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
                      <span className="text-muted-foreground">Proj. Pts/Wk</span>
                      <span className="font-medium tabular-nums">
                        {formatPoints(projectedPoints)}
                      </span>
                    </div>
                  ) : null}
                </div>

                <TeamRosterDialog team={team} />
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
