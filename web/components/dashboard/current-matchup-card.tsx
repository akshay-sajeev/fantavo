"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { TiltCard } from "@/components/ui/motion";
import type { ScheduleResponse } from "@/lib/types";

/**
 * "Current matchup" for a league with zero completed weeks (see
 * docs/decisions.md Phase 5b): there is no played "current week" to report,
 * so this renders schedule.current_week (the first week with an undecided
 * matchup, computed server-side in sim.api.schedule_view -- never
 * fabricated here) explicitly labeled "Upcoming", not as a result. If every
 * matchup is already decided, current_week is null and this renders a
 * "season complete" state instead of guessing a week number.
 */
export function CurrentMatchupCard({ schedule }: { schedule: ScheduleResponse }) {
  const week = schedule.current_week;
  const matchups = week ? schedule.weeks[week - 1] : [];

  return (
    <TiltCard>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <CardTitle className="font-heading text-base">
            {week ? `Week ${week}` : "Schedule"}
          </CardTitle>
          {week ? (
            <Badge variant="outline" className="border-brand-accent/40 bg-brand-accent/15 text-brand-accent">
              Upcoming
            </Badge>
          ) : (
            <Badge variant="outline">Season complete</Badge>
          )}
        </CardHeader>
        <CardContent>
          {!week ? (
            <p className="text-sm text-muted-foreground">
              Every matchup in this schedule already has a decided winner.
            </p>
          ) : matchups.length === 0 ? (
            <p className="text-sm text-muted-foreground">No matchups scheduled for this week.</p>
          ) : (
            <ul className="divide-y divide-border">
              {matchups.map((m) => (
                <li
                  key={`${m.home_team_id}-${m.away_team_id}`}
                  className="flex items-center justify-between gap-3 py-2 text-sm"
                >
                  <span className="font-medium">{m.home_team_name ?? "TBD"}</span>
                  <span className="text-xs text-muted-foreground">vs</span>
                  <span className="text-right font-medium">{m.away_team_name ?? "TBD"}</span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </TiltCard>
  );
}
