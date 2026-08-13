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
  /** false for a bench/IR player ESPN has no usable season projection for
   * -- the numeric fields below are null in that case. Never false for a
   * starter. */
  has_projection: boolean;
  mean: number | null;
  sd: number | null;
  availability: number | null;
  floor: number | null;
  ceiling: number | null;
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

/** Body shape for POST /league/{id}/whatif -- mirrors sim.api.app.WhatIfRequest
 * field-for-field. `roster_overrides` maps team_id -> the full ordered list
 * of player_ids to use as that team's new starters; a team omitted from the
 * map keeps its real ingested roster. */
export interface WhatIfRequestBody {
  season_id?: number;
  roster_overrides: Record<number, number[]>;
  n_sims?: number;
  seed?: number;
}

/** Result of POST /api/league/{id}/whatif-compare (a Next.js route handler,
 * not the sim API directly) -- two whatif calls against the same seed, one
 * with no overrides (the baseline) and one with the scenario's overrides,
 * so "before" and "after" are directly comparable (common random numbers). */
export interface WhatIfCompareResponse {
  seed: number;
  before: SimulationResponse;
  after: SimulationResponse;
}

export interface SeasonReplayTeam {
  team_id: number;
  team_name: string;
  actual_wins: number;
  actual_losses: number;
  actual_ties: number;
  optimal_wins: number;
  optimal_losses: number;
  optimal_ties: number;
  actual_points_for: number;
  optimal_points_for: number;
  neutral_expected_wins: number;
  neutral_expected_losses: number;
  neutral_expected_ties: number;
}

/** Mirrors sim.api.app.SeasonReplayResponse. `note`/`synthetic_actual_scores`
 * are always present and must be surfaced wherever this data renders -- see
 * that response model's docstring and docs/decisions.md Phase 6. */
export interface SeasonReplayResponse {
  league_id: number;
  season_id: number;
  n_regular_weeks: number;
  seed: number;
  synthetic_actual_scores: boolean;
  note: string;
  teams: SeasonReplayTeam[];
}
