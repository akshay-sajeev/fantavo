"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { MatchupRow } from "@/components/dashboard/matchup-row";
import { cn } from "@/lib/utils";
import type { ScheduleResponse } from "@/lib/types";

/**
 * Replaces the old "Remaining schedule" list with a week picker: any week
 * (not just upcoming ones) can be inspected, using the same MatchupRow
 * layout as the dashboard's current-matchup hero card so matchups render
 * identically everywhere on the page. Never fabricates a score -- like
 * CurrentMatchupCard, this only ever shows scheduled pairings, never a
 * result, since no actual weekly results are ingested yet (see
 * CurrentMatchupCard's docstring for the same rule).
 *
 * Defaults to the week AFTER schedule.current_week, because this card sits
 * directly beneath CurrentMatchupCard on the dashboard -- both defaulting to
 * the current week would stack the same matchups twice. The pair reads as
 * "this week, pinned" plus "browse any other week"; every week including the
 * current one is still selectable. When the season is already fully decided
 * (current_week is null) or the current week is the last one, this falls back
 * to the final week rather than an empty/past-the-end state.
 */
export function WeekMatchupsCard({
  schedule,
  className,
}: {
  schedule: ScheduleResponse;
  className?: string;
}) {
  const lastWeek = schedule.weeks.length;
  const defaultWeek = schedule.current_week
    ? Math.min(schedule.current_week + 1, lastWeek)
    : lastWeek;
  const [selectedWeek, setSelectedWeek] = useState(defaultWeek);
  const matchups = schedule.weeks[selectedWeek - 1] ?? [];

  return (
    <Card className={cn("h-full", className)}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="font-heading text-base">Matchups</CardTitle>
        <Select
          value={selectedWeek}
          onValueChange={(value) => {
            if (value != null) setSelectedWeek(value);
          }}
        >
          <SelectTrigger aria-label="Select week">
            <SelectValue>{(value: number | null) => (value == null ? "" : `Week ${value}`)}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {schedule.weeks.map((_, i) => (
              <SelectItem key={i + 1} value={i + 1}>
                Week {i + 1}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 overflow-y-auto">
        {matchups.length === 0 ? (
          <p className="text-sm text-muted-foreground">No matchups scheduled for this week.</p>
        ) : (
          <ul className="divide-y divide-border">
            {matchups.map((m) => (
              <li key={`${m.home_team_id}-${m.away_team_id}`} className="py-2 text-sm">
                <MatchupRow homeTeamName={m.home_team_name} awayTeamName={m.away_team_name} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
