import { Card, CardContent } from "@/components/ui/card";
import type { BracketMatchup } from "@/lib/types";

function MatchupCard({ matchup }: { matchup: BracketMatchup }) {
  return (
    <div className="flex-1 rounded-lg border border-border bg-muted/40 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold tabular-nums text-muted-foreground">
          #{matchup.high_seed}
        </span>
        <span className="text-sm font-medium text-foreground">
          {matchup.high_seed_team_name ?? "TBD"}
        </span>
      </div>
      <div className="my-1.5 text-center text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
        vs
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold tabular-nums text-muted-foreground">
          #{matchup.low_seed}
        </span>
        <span className="text-sm font-medium text-foreground">
          {matchup.low_seed_team_name ?? "TBD"}
        </span>
      </div>
    </div>
  );
}

/**
 * The single projected bracket -- round 1 only, seeded from the
 * maximum-weight assignment of teams to seed slots (see
 * sim.api.playoff_planner_view). Later rounds are deliberately not named
 * with specific teams: which two round-1 winners would actually meet is
 * itself uncertain, so naming a pair there would fabricate certainty this
 * projection doesn't have -- shown instead as a plain "winner of / winner
 * of" placeholder built only from how many round-1 matchups exist, no
 * additional data needed.
 */
export function BracketPanel({ bracket }: { bracket: BracketMatchup[] }) {
  if (bracket.length === 0) return null;

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-3 sm:flex-row">
        {bracket.map((matchup, i) => (
          <MatchupCard key={i} matchup={matchup} />
        ))}
      </div>
      {bracket.length > 1 && (
        <Card className="border-dashed bg-transparent">
          <CardContent className="text-center text-xs text-muted-foreground">
            Championship: winner of{" "}
            {bracket.map((_, i) => `Matchup ${i + 1}`).join(" vs. winner of ")} -- not projected
            with specific teams, since round-1 outcomes are themselves uncertain.
          </CardContent>
        </Card>
      )}
    </div>
  );
}
