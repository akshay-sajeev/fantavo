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

/**
 * Mirrors sim.api.app.SlotPlayoffStrengthOut / sim.api.playoff_planner_view.SlotPlayoffStrength.
 * `floor_ratio_delta = regular_floor_ratio - playoff_floor_ratio`: positive
 * means this slot's bad-week floor is proportionally worse in the
 * compressed playoff window than over a full season -- see that module's
 * docstring for why this number alone is nearly identical across every team
 * at a given slot, and why `has_bench_depth` (a real per-team fact) is what
 * actually decides `is_playoff_specific_weakness`.
 */
export interface SlotPlayoffStrength {
  slot_label: string;
  regular_mean_points_per_week: number;
  playoff_mean_points_per_week: number;
  regular_floor_points_per_week: number;
  playoff_floor_points_per_week: number;
  regular_floor_ratio: number;
  playoff_floor_ratio: number;
  floor_ratio_delta: number;
  has_bench_depth: boolean;
  /** False for a slot resolving to K or D/ST -- those are single-slot
   * positions a real roster normally carries exactly one of, so zero bench
   * depth there is never treated as a weakness (see
   * sim.api.playoff_planner_view's `_BENCH_DEPTH_RELEVANT_POSITIONS`). */
  bench_depth_relevant: boolean;
  /** 1 = the league's strongest projected playoff-weeks scorer at this slot. */
  league_rank: number;
  league_team_count: number;
  /** 0-100; 100 = strongest in the league at this slot. */
  league_percentile: number;
  is_playoff_specific_weakness: boolean;
}

/** Mirrors sim.api.app.PlayoffSeedOddsOut. `seed_probabilities[s]` is the
 * probability this team ends the regular season holding seed `s` (index 0 =
 * top seed) -- length n_playoff_teams, summing to `playoff_probability`. */
export interface PlayoffSeedOdds {
  team_id: number;
  team_name: string;
  title_probability: number;
  playoff_probability: number;
  reached_final_probability: number;
  finish_distribution: number[];
  seed_probabilities: number[];
  /** 1-indexed seed this team is assigned in the single projected bracket,
   * or null if the maximum-weight assignment did not select it. */
  projected_seed: number | null;
}

/** Mirrors sim.api.app.BracketMatchupOut. Round 1 of the single projected
 * bracket only -- see sim.api.playoff_planner_view for why a later round
 * (e.g. the final) is intentionally not named with specific teams. */
export interface BracketMatchup {
  high_seed: number;
  high_seed_team_id: number | null;
  high_seed_team_name: string | null;
  low_seed: number;
  low_seed_team_id: number | null;
  low_seed_team_name: string | null;
}

export interface TeamPlayoffPlan {
  team_id: number;
  team_name: string;
  slot_strengths: SlotPlayoffStrength[];
  weakest_slot: string | null;
  /** A real narrative synthesized server-side from this team's own
   * slot-strength numbers -- never computed or reworded in a component. */
  recommendation: string;
}

/** Mirrors sim.api.app.PlayoffPlannerResponse. */
export interface PlayoffPlannerResponse {
  league_id: number;
  season_id: number;
  n_regular_weeks: number;
  n_playoff_rounds: number;
  n_playoff_teams: number;
  seed: number;
  n_sims: number;
  seeding: PlayoffSeedOdds[];
  bracket: BracketMatchup[];
  teams: TeamPlayoffPlan[];
}

/** Mirrors sim.api.app.LineupSlotAssignmentOut. `is_swap` is true when this
 * player is NOT who the team's actual ingested lineup starts in this slot
 * -- server-computed, so the UI never has to re-derive "what changed" by
 * comparing player_ids itself. */
export interface LineupSlotAssignment {
  slot_label: string;
  player_id: number;
  player_name: string;
  position: string;
  is_swap: boolean;
}

/**
 * Mirrors sim.api.app.LineupProjectionOut / sim.api.lineup_optimizer_view.LineupProjection.
 * `weekly_floor`/`weekly_ceiling` are the 10th/90th percentile of real Monte
 * Carlo samples of this exact lineup's TEAM TOTAL for one week -- never a
 * sum of individual player floors/ceilings (see that module's docstring for
 * why the distinction matters). `title_probability`/`playoff_probability`/
 * `finish_distribution` come from a real `simulate_seasons()` call with
 * only this one team's lineup overridden to this candidate.
 */
export interface LineupProjection {
  label: string;
  assignments: LineupSlotAssignment[];
  weekly_mean: number;
  weekly_floor: number;
  weekly_ceiling: number;
  title_probability: number;
  playoff_probability: number;
  finish_distribution: number[];
}

/** Mirrors sim.api.app.LineupOptimizerResponse. `n_candidates_considered`
 * is the baseline plus every single-slot swap this team's search actually
 * evaluated -- see sim.api.lineup_optimizer_view's module docstring for
 * exactly what search space that is and why. */
export interface LineupOptimizerResponse {
  league_id: number;
  season_id: number;
  team_id: number;
  team_name: string;
  seed: number;
  weekly_n_sims: number;
  season_n_sims: number;
  n_candidates_considered: number;
  current: LineupProjection;
  safest: LineupProjection;
  highest_upside: LineupProjection;
}

/**
 * Mirrors sim.api.app.WaiverCandidateOut / sim.api.waiver_intelligence_view.WaiverCandidate.
 * `percent_owned` / `percent_started` / `percent_change` / `average_draft_position`
 * are ESPN's cross-league consensus ownership data (see
 * `WaiverIntelligenceResponse.ownership_data_note`), already expressed on a
 * 0-100 scale -- NOT a 0-1 fraction like `formatPercent` elsewhere in this
 * app expects, so render these with `formatPercentPoints`, not
 * `formatPercent`. `opportunity_score` / `start_rate_ratio` ARE 0-1
 * fractions (`formatPercent` applies to those). `expected_playable_points =
 * mean_points_per_game * opportunity_score` -- see that module's docstring
 * for why this, not a bigger invented composite, ranks candidates within a
 * position group.
 */
export interface WaiverCandidate {
  player_id: number;
  player_name: string;
  injury_status: string | null;
  mean_points_per_game: number;
  /** games_projected / 17 -- a season-long games-missed forecast, distinct
   * from opportunity_score's week-to-week role-trust signal. */
  season_availability: number;
  percent_owned: number;
  percent_started: number;
  percent_change: number;
  average_draft_position: number | null;
  start_rate_ratio: number;
  opportunity_score: number;
  expected_playable_points: number;
  /** A real narrative synthesized server-side from this player's own
   * Opportunity/Availability signals -- never computed or reworded in a
   * component. */
  reasoning: string;
}

/** Mirrors sim.api.app.WaiverPositionGroupOut. One position's worth of
 * ranked free-agent candidates for one specific requesting team, plus the
 * League fit / Competition facts -- position-level, not player-level, so
 * they live here rather than repeated on every candidate. See
 * sim.api.waiver_intelligence_view's module docstring, especially "Why this
 * is grouped by position, not one flat list". `bench_depth_relevant` is
 * false for K/D-ST: bench depth isn't a real roster decision at those two
 * positions in this league (verified directly against the real fixture),
 * so `team_has_positional_need`/`rival_teams_with_need` are always
 * false/[] there rather than a fabricated need signal. */
export interface WaiverPositionGroup {
  position: string;
  bench_depth_relevant: boolean;
  team_bench_depth_at_position: number;
  team_has_positional_need: boolean;
  team_starters_at_position: string[];
  rival_teams_with_need: string[];
  group_reasoning: string;
  candidates: WaiverCandidate[];
}

/** Mirrors sim.api.app.WaiverIntelligenceResponse. */
export interface WaiverIntelligenceResponse {
  league_id: number;
  season_id: number;
  team_id: number;
  team_name: string;
  ownership_data_note: string;
  groups: WaiverPositionGroup[];
}

/**
 * Mirrors sim.api.app.TeamLeagueProfileOut / sim.api.beat_my_league_view.TeamLeagueProfile.
 * `strengths`/`weaknesses` are a pure selection over Playoff Planner's own
 * `SlotPlayoffStrength` list (never a new formula) -- `weaknesses` is
 * always 0 or 1 entries (Playoff Planner's own single `weakest_slot`, not
 * every slot that clears `is_playoff_specific_weakness`; see
 * sim.api.beat_my_league_view's module docstring for why the raw flagged
 * set is not team-differentiating in this league -- K clears it for every
 * real team). `playoff_schedule_note` is Playoff Planner's own
 * server-synthesized recommendation, surfaced verbatim.
 */
export interface TeamLeagueProfile {
  team_id: number;
  team_name: string;
  title_probability: number;
  playoff_probability: number;
  finish_distribution: number[];
  strengths: SlotPlayoffStrength[];
  weaknesses: SlotPlayoffStrength[];
  playoff_schedule_note: string;
}

/** Mirrors sim.api.app.RivalThreatOut. `overlapping_slots` is empty when no
 * rival has a real structural strength at the user's own real weakness --
 * an honest fallback (named by highest title odds league-wide instead), not
 * a bug -- see `reasoning` for which case fired. */
export interface RivalThreat {
  team_id: number;
  team_name: string;
  title_probability: number;
  overlapping_slots: string[];
  reasoning: string;
}

export interface TeamAdvantage {
  slots: string[];
  reasoning: string;
}

/** Mirrors sim.api.app.TradeCautionOut. A real bench-depth surplus at
 * `position` for the requesting team, at least one rival with zero depth
 * there (the same rule sim.api.waiver_intelligence_view's Signal 4 already
 * computes, applied to trades instead of waivers). */
export interface TradeCaution {
  position: string;
  team_bench_depth_at_position: number;
  bench_player_names: string[];
  rival_teams_with_need: string[];
  reasoning: string;
}

/** Mirrors sim.api.app.BeatMyLeagueResponse. `trade_cautions` can be a real,
 * honest empty array for a team with no positional trade leverage anywhere
 * in the league right now. */
export interface BeatMyLeagueResponse {
  league_id: number;
  season_id: number;
  team_id: number;
  team_name: string;
  seed: number;
  n_sims: number;
  teams: TeamLeagueProfile[];
  biggest_threat: RivalThreat;
  real_advantage: TeamAdvantage;
  trade_cautions: TradeCaution[];
}

/** Mirrors sim.api.app.RoastFactOut / sim.api.roast_view.RoastFact. One
 * concrete, already-computed fact backing one sentence of a roast -- `kind`
 * is a stable tag ("title_rank" | "draft_reach" | "draft_best_pick" |
 * "draft_structural" | "bench_depth" | "rival_threat"), `text` is the real
 * citation (a rank, a player name, a percentage) the joke is grounded in. */
export interface RoastFact {
  kind: string;
  text: string;
}

/** Mirrors sim.api.app.TeamRoastOut. `title_rank` is this team's 1-indexed
 * position in the power rankings (1 = best) -- `teams` on the enclosing
 * response is already sorted by this. Every sentence in `roast` corresponds
 * 1:1 to an entry in `facts`, in the same order -- see
 * sim.api.roast_view's module docstring. */
export interface TeamRoast {
  team_id: number;
  team_name: string;
  title_probability: number;
  title_rank: number;
  league_team_count: number;
  roast: string;
  facts: RoastFact[];
}

/** Mirrors sim.api.app.PowerRankingRoastResponse. `has_draft_data` is false
 * for a league with no completed draft to grade (e.g. the SYNTHETIC
 * validation league) -- every roast still renders, just without a
 * draft-derived sentence/fact. `teams` is already ordered by
 * title_probability descending (the power-ranking order). */
export interface PowerRankingRoastResponse {
  league_id: number;
  season_id: number;
  seed: number;
  n_sims: number;
  has_draft_data: boolean;
  teams: TeamRoast[];
}

/** Mirrors sim.api.app.AnalystMessageIn. One turn of the persisted chat
 * transcript. The sim API keeps no session state -- the client resends the
 * full transcript (oldest first) on every request. */
export interface AnalystMessage {
  role: "user" | "model";
  text: string;
}

/** Mirrors sim.api.app.AnalystCitationOut / sim.api.analyst_view.Citation.
 * One real numeric fact extracted verbatim from a tool result this turn --
 * `percent` is always on a 0-100 scale regardless of which tool it came
 * from, so every citation renders the same way. Never computed by this
 * app's web layer. */
export interface AnalystCitation {
  index: number;
  source_tool: string;
  kind: string;
  subject: string;
  percent: number;
  display: string;
}

/** Mirrors sim.api.app.AnalystSpanOut / sim.api.analyst_view.CitationSpan.
 * A character range in the enclosing message's `reply` text where a real
 * citation's value was found -- see sim.api.analyst_view's module
 * docstring for the exact (deterministic, server-side) matching rule. The
 * frontend only ever slices `reply` at these offsets and renders a
 * <StatChip> in place of the plain text; it never decides which numbers
 * are "real" itself. */
export interface AnalystSpan {
  start: number;
  end: number;
  citation_index: number;
}

/** Mirrors sim.api.app.AnalystToolCallOut. One real tool invocation this
 * turn made, verbatim -- kept so a reader (or this phase's own manual
 * verification step) can cross-check any number in `reply` against the
 * exact tool result it came from. */
export interface AnalystToolCall {
  name: string;
  args: Record<string, unknown>;
  result: Record<string, unknown>;
}

/** Mirrors sim.api.app.AnalystChatResponse. */
export interface AnalystChatResponse {
  league_id: number;
  season_id: number;
  team_id: number;
  team_name: string;
  reply: string;
  citations: AnalystCitation[];
  spans: AnalystSpan[];
  tool_calls: AnalystToolCall[];
}
