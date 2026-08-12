/**
 * Mirrors the pydantic response models in sim/api/app.py field-for-field.
 * Nothing here is computed -- these types describe exactly what the API
 * returns, so a mismatch here is a bug in this file, not a place to smooth
 * one over with an extra derived field.
 */

export interface TeamOutcome {
  team_id: number;
  team_name: string;
  title_probability: number;
  playoff_probability: number;
  reached_final_probability: number;
  mean_wins: number;
  mean_points_for: number;
  /** finish_distribution[p] = probability of finishing in place p (0 = champion). */
  finish_distribution: number[];
}

export interface SimulationResponse {
  league_id: number;
  season_id: number;
  n_sims: number;
  seed: number;
  computed_at: string;
  teams: TeamOutcome[];
}

export interface RosterPlayer {
  player_id: number;
  name: string;
  position: string;
  lineup_slot: string;
  is_starter: boolean;
  mean: number;
  sd: number;
  availability: number;
  floor: number;
  ceiling: number;
}

export interface TeamRoster {
  team_id: number;
  team_name: string;
  starters: RosterPlayer[];
  bench: RosterPlayer[];
  risk_rating: number;
  positional_concentration: string[];
}

export interface RosterResponse {
  league_id: number;
  season_id: number;
  teams: TeamRoster[];
}

export interface ScheduledMatchup {
  week: number;
  home_team_id: number | null;
  home_team_name: string | null;
  away_team_id: number | null;
  away_team_name: string | null;
  winner: string | null;
}

export interface ScheduleResponse {
  league_id: number;
  season_id: number;
  n_regular_weeks: number;
  current_week: number | null;
  weeks: ScheduledMatchup[][];
}
