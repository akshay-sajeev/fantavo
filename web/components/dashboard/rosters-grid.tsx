import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { TeamRoster } from "@/lib/types";

/**
 * Compact per-team roster listing for the dashboard overview -- names and
 * positions only. Availability/floor/ceiling/risk live on the dedicated
 * Risk panel (/risk), not duplicated here, so this stays scannable. Native
 * <details>/<summary> for progressive disclosure: keyboard- and
 * screen-reader-accessible with no client JS needed, which matters since
 * this whole page is a Server Component.
 */
export function RostersGrid({ teams }: { teams: TeamRoster[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-base">Rosters</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {teams.map((team) => (
            <details
              key={team.team_id}
              className="group rounded-lg border border-border p-3 transition-colors duration-150 open:bg-muted/40 hover:bg-muted/25"
            >
              <summary className="cursor-pointer list-none font-medium marker:hidden focus-visible:outline-2 focus-visible:outline-ring">
                <span className="flex items-center justify-between gap-2">
                  <span>{team.team_name}</span>
                  <span className="text-xs text-muted-foreground group-open:hidden">
                    {team.starters.length} starters
                  </span>
                </span>
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
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
