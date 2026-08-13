import Link from "next/link";
import { cn } from "@/lib/utils";

/**
 * Team switcher as plain links carrying `?team=<id>` -- same server-
 * navigated pattern `components/lineup/team-picker.tsx` and
 * `components/waivers/team-picker.tsx` already established for this app's
 * other per-team analysis pages: this page's computation is a real search
 * (reuses Playoff Planner's full simulate_seasons() + per-slot sampling
 * pipeline), not a cheap client-side toggle, and no auth/league-picker
 * exists in this app -- see docs/decisions.md Phase 5b -- so this is how
 * "the user's team" gets selected.
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
    <nav className="flex gap-1 overflow-x-auto pb-1" aria-label="Choose your team">
      {teams.map((team) => {
        const isActive = team.team_id === selectedTeamId;
        return (
          <Link
            key={team.team_id}
            href={`/league/${leagueId}/beat-my-league?team=${team.team_id}`}
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
