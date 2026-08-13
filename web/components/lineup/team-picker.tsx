import Link from "next/link";
import { cn } from "@/lib/utils";

/**
 * Team switcher as plain links carrying `?team=<id>` -- a full server
 * navigation per team (this page's computation is a live, non-trivial
 * search, not a cheap read), matching this app's established pattern of
 * server-rendered pages driven by the URL rather than client state (e.g.
 * dashboard/power-rankings/playoffs), not the client-side `useState`
 * pattern the What-If page uses for its own in-place scenario runs.
 */
export function TeamPicker({
  leagueId,
  teams,
  selectedTeamId,
}: {
  leagueId: number;
  teams: { team_id: number; team_name: string }[];
  selectedTeamId: number;
}) {
  return (
    <nav className="flex gap-1 overflow-x-auto pb-1" aria-label="Choose a team">
      {teams.map((team) => {
        const isActive = team.team_id === selectedTeamId;
        return (
          <Link
            key={team.team_id}
            href={`/league/${leagueId}/lineup-optimizer?team=${team.team_id}`}
            aria-current={isActive ? "page" : undefined}
            className={cn(
              "shrink-0 cursor-pointer rounded-md border px-3 py-1.5 text-sm font-medium transition-colors duration-150",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring",
              isActive
                ? "border-primary bg-primary text-primary-foreground"
                : "border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {team.team_name}
          </Link>
        );
      })}
    </nav>
  );
}
