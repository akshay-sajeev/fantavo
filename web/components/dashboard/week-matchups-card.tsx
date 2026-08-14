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
 * Defaults to schedule.current_week when one exists; when the season is
 * already fully decided (current_week is null), defaults to the last
 * regular-season week instead of an empty/past-the-end state, since this is
 * a "pick any week" browser now, not a "what's left" list.
 */
export function WeekMatchupsCard({ schedule }: { schedule: ScheduleResponse }) {
  const defaultWeek = schedule.current_week ?? schedule.n_regular_weeks;
  const [selectedWeek, setSelectedWeek] = useState(defaultWeek);
  const matchups = schedule.weeks[selectedWeek - 1] ?? [];

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="font-heading text-base">Matchups</CardTitle>
        <Select
          value={selectedWeek}
          onValueChange={(value) => {
            if (value != null) setSelectedWeek(value);
          }}
        >
          <SelectTrigger aria-label="Select week">
            <SelectValue>{(value: number) => `Week ${value}`}</SelectValue>
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
      <CardContent>
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
