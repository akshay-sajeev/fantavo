import { Badge } from "@/components/ui/badge";

/**
 * Shared row layout for a single matchup, used by both the dashboard's
 * current-matchup hero card and the week selector -- a fixed
 * grid-cols-[1fr_auto_1fr] with truncation on each team name means a long
 * name can never push the centered "VS" pill out of alignment (the bug this
 * fixes), since the middle column's width never depends on its neighbors'
 * content length.
 */
export function MatchupRow({
  homeTeamName,
  awayTeamName,
}: {
  homeTeamName: string | null;
  awayTeamName: string | null;
}) {
  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
      <span className="min-w-0 truncate text-left font-medium">{homeTeamName ?? "TBD"}</span>
      <Badge variant="secondary" className="shrink-0">
        VS
      </Badge>
      <span className="min-w-0 truncate text-right font-medium">{awayTeamName ?? "TBD"}</span>
    </div>
  );
}
