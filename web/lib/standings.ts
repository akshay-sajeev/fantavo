import type { ScheduleResponse } from "@/lib/types";

export interface ActualRecord {
  team_id: number;
  wins: number;
  losses: number;
  ties: number;
  games_played: number;
}

const DECIDED_WINNERS = new Set(["HOME", "AWAY", "TIE"]);

/**
 * Tallies already-decided matchups (`winner` already "HOME"/"AWAY"/"TIE" in
 * the schedule response, exactly as the matchup table stores it) into a
 * plain win/loss/tie count per team.
 *
 * This is bookkeeping over facts the API already returned -- literal
 * counting of decided game outcomes, no projection, no weighting, no
 * probability, nothing simulate_seasons() computes differently. It is not
 * the kind of "analytics logic" CLAUDE.md's "no analytics logic in
 * components" rule is aimed at (which is about not re-deriving simulated
 * outcomes client-side); it's the same class of operation as sorting an
 * already-returned array. See docs/decisions.md Phase 5b.
 */
export function tallyActualRecords(schedule: ScheduleResponse): Map<number, ActualRecord> {
  const records = new Map<number, ActualRecord>();
  const ensure = (teamId: number): ActualRecord => {
    let r = records.get(teamId);
    if (!r) {
      r = { team_id: teamId, wins: 0, losses: 0, ties: 0, games_played: 0 };
      records.set(teamId, r);
    }
    return r;
  };

  for (const week of schedule.weeks) {
    for (const m of week) {
      if (m.home_team_id == null || m.away_team_id == null) continue;
      if (!m.winner || !DECIDED_WINNERS.has(m.winner)) continue;

      const home = ensure(m.home_team_id);
      const away = ensure(m.away_team_id);
      if (m.winner === "HOME") {
        home.wins += 1;
        away.losses += 1;
      } else if (m.winner === "AWAY") {
        away.wins += 1;
        home.losses += 1;
      } else {
        home.ties += 1;
        away.ties += 1;
      }
      home.games_played += 1;
      away.games_played += 1;
    }
  }
  return records;
}

export function totalGamesPlayed(records: Map<number, ActualRecord>): number {
  let total = 0;
  for (const r of records.values()) total += r.games_played;
  return total / 2; // every played game increments both teams
}
