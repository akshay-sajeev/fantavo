import { Info } from "lucide-react";

/**
 * Honest framing, visible in the UI itself and not just in
 * docs/decisions.md -- see sim.api.lineup_optimizer_view's module
 * docstring for the full reasoning. `simulate_seasons()`'s roster override
 * is season-long, not per-week, and that happens to match this league's
 * real state today (every matchup is still UNDECIDED -- the season hasn't
 * started, so "this week" and "the rest of the season" are the same
 * question right now). Once real weeks are played, this framing will need
 * revisiting -- a future phase's territory, not silently pretended
 * permanent here.
 */
export function FramingNote({
  nCandidatesConsidered,
  weeklyNSims,
  seasonNSims,
}: {
  nCandidatesConsidered: number;
  weeklyNSims: number;
  seasonNSims: number;
}) {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
      <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <p className="leading-relaxed">
        These lineups are evaluated for the rest of the season, not just one week -- the
        simulator has no per-week lineup mechanism yet, and since this league hasn&apos;t played
        a game yet, &quot;this week&quot; and &quot;the rest of the season&quot; are currently the
        same question. Evaluated {nCandidatesConsidered.toLocaleString()} legal lineups
        (the current lineup plus every single-slot swap a real bench player is eligible for):
        each lineup&apos;s weekly range comes from {weeklyNSims.toLocaleString()} Monte Carlo
        samples of the team&apos;s own total, and its title/playoff odds come from{" "}
        {seasonNSims.toLocaleString()} simulated seasons.
      </p>
    </div>
  );
}
