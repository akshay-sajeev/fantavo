import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ScheduleResponse } from "@/lib/types";

/**
 * Every week from `current_week` onward (or the whole schedule if every
 * matchup is already decided). Rendered as scheduled/projected matchups --
 * never a score, since no actual weekly results are ingested yet (see
 * CurrentMatchupCard's docstring for the same "don't fabricate a played
 * game" rule).
 */
export function RemainingScheduleTable({ schedule }: { schedule: ScheduleResponse }) {
  const startWeek = schedule.current_week ?? schedule.n_regular_weeks + 1;
  const remaining = schedule.weeks
    .map((matchups, i) => ({ week: i + 1, matchups }))
    .filter((w) => w.week >= startWeek);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-base">Remaining schedule</CardTitle>
      </CardHeader>
      <CardContent>
        {remaining.length === 0 ? (
          <p className="text-sm text-muted-foreground">No remaining games this season.</p>
        ) : (
          <div className="max-h-96 overflow-y-auto pr-1">
            <ol className="space-y-3">
              {remaining.map(({ week, matchups }) => (
                <li key={week}>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Week {week}
                  </p>
                  <ul className="space-y-1">
                    {matchups.map((m) => (
                      <li
                        key={`${m.home_team_id}-${m.away_team_id}`}
                        className="flex items-center justify-between text-sm"
                      >
                        <span>{m.home_team_name ?? "TBD"}</span>
                        <span className="text-xs text-muted-foreground">vs</span>
                        <span className="text-right">{m.away_team_name ?? "TBD"}</span>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ol>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
