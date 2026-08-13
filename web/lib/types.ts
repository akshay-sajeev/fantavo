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

/**
 * Mirrors sim.api.app.DraftPickGradeOut / sim.api.draft_autopsy_view.DraftPickGrade
 * field-for-field. `value_gap = alternative_player_rank - player_rank` when
 * an alternative existed: positive means the player taken was ranked BETTER
 * than the best same-position alternative left on the board (a
 * correct-process pick); negative means a better-ranked alternative at the
 * same position was passed over (a reach). Zero with `alternative_player_id
 * === null` means no alternative existed in the tracked player pool at that
 * point in the draft -- not graded either way.
 */
export interface DraftPickGrade {
  overall_pick_number: number;
  round_id: number;
  round_pick_number: number;
  team_id: number;
  team_name: string;
  player_id: number;
  player_name: string;
  position: string;
  slot_label: string;
  /** One of "QB" | "RB" | "WR" | "TE" | "Bench", or null for a K/D-ST pick
   * -- see sim.api.draft_autopsy_view's module docstring for why K/D-ST are
   * excluded from the positional-strategy-grade summary. */
  grade_bucket: string | null;
  player_rank: number;
  player_adp: number | null;
  alternative_player_id: number | null;
  alternative_player_name: string | null;
  alternative_player_rank: number | null;
  value_gap: number;
  best_overall_available_player_id: number | null;
  best_overall_available_player_name: string | null;
  best_overall_available_rank: number | null;
}

export interface PositionGrade {
  position: string;
  pick_count: number;
  avg_value_gap: number;
  league_avg_value_gap: number;
  label: string;
}

export interface PositionTiming {
  position: string;
  team_first_pick_number: number;
  team_first_pick_round: number;
  league_avg_first_pick_number: number;
  team_pick_count: number;
  team_avg_value_gap: number;
  league_avg_value_gap: number;
}

export interface TeamDraftAutopsy {
  team_id: number;
  team_name: string;
  picks: DraftPickGrade[];
  best_pick: DraftPickGrade;
  worst_pick: DraftPickGrade;
  position_grades: PositionGrade[];
  position_timing: PositionTiming[];
  /** A real narrative synthesized server-side from this team's own
   * pick-level data -- never computed or reworded in a component, per
   * CLAUDE.md's "no analytics logic in components" rule. */
  structural_finding: string;
}

/** Mirrors sim.api.app.DraftAutopsyResponse. */
export interface DraftAutopsyResponse {
  league_id: number;
  season_id: number;
  /** Human-readable label for the rank signal used throughout -- see
   * sim.api.draft_autopsy_view's module docstring and docs/decisions.md
   * Phase 7 for the full data-provenance reasoning. */
  rank_source: string;
  teams: TeamDraftAutopsy[];
}
