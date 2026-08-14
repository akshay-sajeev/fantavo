"""Playoff Planner, backing `GET /league/{id}/playoff-planner`.

Covers PLAN.md's Phase 8 (feature 12): a projected playoff bracket seeded
from simulated regular-season outcomes, per-seed odds, weeks-strength-per-
roster-slot for the playoff weeks, and a positional weakness that is
specific to the short playoff window rather than a restatement of a team's
full-season grade.

--------------------------------------------------------------------------
"Restricted to the league's own playoff weeks" -- what that means here:

PLAN.md's text says to "run the simulator restricted to weeks 15-17."
Read literally that would mean calling `simulate_seasons()` with only the
playoff-window weeks as input, but that is not how playoff outcomes can be
produced at all: which two teams a bracket seed slot even *contains* is
itself an output of the regular season (`simulate_seasons()`'s own
seeding/`seed_rank` logic, see below), so the playoff weeks cannot be
simulated in isolation from the regular season that determines who is in
them. `raw["schedule"]` also contains zero playoff-round entries for this
league regardless (ESPN does not publish playoff pairings until seeding is
real) -- there is no real playoff schedule to restrict a simulation to.

What IS restricted to the playoff weeks is this module's *output*:
`simulate_seasons()` is called completely unmodified, exactly as every
other feature calls it (full regular season + `n_playoff_rounds`, per its
own docstring), and this module reports only on the playoff-relevant slice
of what it already returns/samples -- `seed_rank` for seeding odds, and a
fresh `_sample_player_weeks` draw restricted in its own output slicing to
just the playoff-window columns (`scores[:, n_regular_weeks:, :]`, the
exact slicing `simulate_seasons()` itself uses to split `regular` from
`playoff_scores`) for the per-slot strength numbers. No second simulation
path, no bracket logic reimplemented -- see the two specific reuses below.

--------------------------------------------------------------------------
Reuse of `sim.engine` internals (CLAUDE.md: "one simulation engine"):

1. Seeding odds and the projected bracket are built entirely from
   `SimulationResult.seed_rank` (n_sims, n_teams), a field `simulate_seasons()`
   already returns -- no re-simulation, a pure post-processing function over
   an existing output tensor, exactly the class of extension CLAUDE.md's
   "one simulation engine" rule explicitly allows ("add ... a
   post-processing function over the same result tensor").

2. The "projected bracket" needs a *single* representative seed assignment,
   not a distribution -- `sim.engine.bracket_pairings()` (added this phase,
   factored out of `sim.engine._run_playoffs`'s own pairing rule so both
   call sites share one implementation, see that function's docstring)
   supplies the exact 1v4/2v3-style structural pairing the engine already
   uses every simulated round. Which team fills which seed slot in that
   single bracket is chosen by `scipy.optimize.linear_sum_assignment`
   (maximum-weight matching over the team x seed probability matrix
   `seed_rank` implies) -- the same optimization technique
   `sim.api.season_replay_view` already established for a structurally
   identical "best assignment of items to slots" problem (there: players to
   starting slots; here: teams to bracket seeds), not a new pattern.

3. Per-roster-slot playoff-window strength reuses `sim.engine._sample_player_weeks`
   directly -- the literal per-player sampling primitive `simulate_seasons()`
   itself calls, the same reuse `sim.api.season_replay_view` already
   established for a different feature that also needs individual player
   scores rather than a team total. Sampled for `n_regular_weeks +
   n_playoff_rounds` weeks and sliced with the identical
   `[:n_regular_weeks]` / `[n_regular_weeks:]` split `simulate_seasons()`
   itself performs, then summed over the players in one starting slot
   instead of over the whole team.

--------------------------------------------------------------------------
The "strength of schedule" data-gap question (see docs/decisions.md Phase 8
for the full writeup): this fixture has no real NFL opponent/defense/game
schedule data for any player anywhere (only `proTeamId`), so a literal
"which real NFL defense is this player facing in week 16" reading of
PLAN.md's phrase is not buildable without inventing data, and is not
attempted here. What IS built is the other, buildable reading PLAN.md's own
UI bullet supports ("schedule strength per roster slot"): how strong a
team's own roster is, position by position, for the specific playoff-length
window, relative to the rest of the league -- i.e. FANTASY opponent
strength (the field of realistic bracket opponents), not real NFL defensive
strength. `league_rank` / `league_percentile` on `SlotPlayoffStrength`
answer exactly that question, from data this codebase actually has.

--------------------------------------------------------------------------
Why a short playoff window can reveal a *different* weakness than a team's
full-season grade, even though no player's underlying weekly distribution
changes between the regular season and the playoffs in this model: the
*mean* expected points per week for a position group is the same random
variable either way (same Gamma(mean, sd) x availability draws), so it
converges to the same value regardless of window length as `n_sims` grows.
What genuinely differs is the *floor* -- the 10th percentile of points
actually delivered -- expressed as a fraction of that group's own mean.
Averaging over 14 regular-season weeks lets a bad week get smoothed out by
several good ones; averaging over only `n_playoff_rounds` (2, for this
league) leaves far less room for that to happen, so a high-variance
position's realistic downside is structurally worse in the compressed
window even though its average is identical. This is a real, derivable
consequence of the same fitted model (the law of large numbers applied to a
shorter sample), not an invented number -- see `floor_ratio_delta` below.

`floor_ratio_delta` alone is *not* team-specific, though: `sim.params`
fits one coefficient-of-variation per *position*, shared by every player at
that position league-wide (see docs/decisions.md Phase 2), and the Gamma
shape parameter that drives a floor/mean ratio is `(mean/sd)^2 = 1/CV^2` --
independent of any individual player's own mean. So `floor_ratio_delta` for
a given slot label lands in a tight, nearly team-invariant band across the
whole league (verified live: e.g. every team's regular-season QB floor
ratio falls within 0.001 of every other team's). It is real and worth
showing (some *positions* are structurally spikier than others, and that is
true information), but on its own it cannot answer PLAN.md's actual ask --
"a position where THIS team's roster is thin," a claim about one team, not
about a position in the abstract.

What genuinely differs *by team* at the same slot is whether that team has
rostered a same-position bench player to fall back on -- exactly
`sim.api.roster_view`'s existing `positional_concentration` idea (zero
same-position bench depth), computed independently here because
`positional_concentration` is keyed to `RosterPlayer`, a different
dataclass shape, but the identical underlying rule: same non-starting-slot
set (`ingest.slots.NON_STARTING_SLOTS`), same "count bench players by their
own `defaultPositionId` label" logic. `is_playoff_specific_weakness`
therefore requires `floor_ratio_delta` clearing the editorial threshold AND
zero same-position bench depth AND the slot resolving to a position where
bench depth is even a real roster decision (`_BENCH_DEPTH_RELEVANT_POSITIONS`
-- K and D/ST are single-slot positions almost never benched, so a
same-position bench count of zero there is a near-constant fact about how
fantasy rosters are built, not a real per-team differentiator, matching
`sim.api.waiver_intelligence_view`'s own already-established exclusion for
the identical reason). The same
"multiple signals together, not just one" discipline
`sim.api.draft_autopsy_view._synthesize_structural_finding` already
established before making a causal claim. Confirmed live against the real
league that this combination genuinely differs team to team (e.g. three of
eight teams carry zero bench TE and are flagged there; the other five carry
bench TE depth and are not), unlike `floor_ratio_delta` alone.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import psycopg
from scipy.optimize import linear_sum_assignment

from ingest.parse import (
    build_team_params,
    parse_player_pool,
    parse_schedule,
    parse_scoring_table,
    parse_teams,
)
from ingest.slots import (
    DEFAULT_POSITION_NAMES,
    NON_STARTING_SLOTS,
    SLOT_NAMES,
    starting_slot_counts,
)
from sim.api.params_loader import load_raw_payload
from sim.api.seeds import precompute_seed
from sim.engine import (
    LeagueParams,
    PlayerParams,
    _next_power_of_two,  # private engine helper, reused not reimplemented -- see module docstring
    _sample_player_weeks,  # private engine helper, reused not reimplemented -- see module docstring
    bracket_pairings,
    simulate_seasons,
)
from sim.params.derive import derive_player_params_pool
from sim.params.variance import fit_position_cv

# Same order of magnitude as sim.api.precompute.PRECOMPUTE_N_SIMS (10,000):
# this endpoint is live-computed per request (like roster/schedule/draft-
# autopsy), not cached, but it is deterministically seeded (see `seed`
# below), so re-running it reproduces the exact same numbers -- there is no
# "waiting user" tradeoff that would call for a smaller live-what-if-style
# n_sims here.
DEFAULT_N_SIMS = 10_000

# 10th percentile, matching sim.api.roster_view's own floor-percentile
# choice (a presentation choice applied consistently across the app, not a
# fitted value) -- np.percentile takes a 0-100 scale, unlike
# scipy.stats.gamma.ppf's 0-1 scale roster_view uses, hence the different
# looking constant for the same underlying idea.
_FLOOR_PERCENTILE_PCT = 10.0

# Editorial threshold, the same class of choice as
# sim.api.draft_autopsy_view's _GRADE_LABEL_THRESHOLD / _LATE_TIMING_THRESHOLD:
# a presentation decision over an already-derived number, not a fitted or
# invented input to simulate_seasons(). A slot's playoff-window floor ratio
# must sit at least 8 percentage points below its full-season floor ratio
# before this module even considers calling it a playoff-specific weakness
# -- see the module docstring for why this alone is not team-specific and is
# combined with a real per-team bench-depth check before actually flagging
# one (`is_playoff_specific_weakness` below).
_WEAKNESS_DELTA_THRESHOLD = 0.08

# The four positions with a real bench-depth decision behind them --
# matching sim.api.roster_view._BENCH_DEPTH_RELEVANT_POSITIONS /
# sim.api.waiver_intelligence_view._BENCH_DEPTH_RELEVANT_POSITIONS /
# sim.api.beat_my_league_view._BENCH_DEPTH_RELEVANT_POSITIONS exactly (the
# rule is shared, reimplemented locally per that established precedent). K
# and D/ST are single-slot positions ESPN-style leagues structurally almost
# never bench -- carrying exactly one of each is a normal, healthy roster,
# so a slot resolving to either position can never be flagged
# `is_playoff_specific_weakness` below, regardless of `has_bench_depth`.
_BENCH_DEPTH_RELEVANT_POSITIONS = frozenset({"QB", "RB", "WR", "TE"})


@dataclass(frozen=True)
class SlotPlayoffStrength:
    """One team's starting-slot strength for the playoff-length window,
    compared against the same slot's full-season profile and against every
    other team in the league at that same slot.

    `floor_ratio = <10th percentile total points for the window> / <mean
    total points for the window>` -- how much of this slot's average output
    still shows up on a bad stretch. `floor_ratio_delta =
    regular_floor_ratio - playoff_floor_ratio`; positive means the floor is
    *worse*, proportionally, in the compressed playoff window than over a
    full season -- see the module docstring for why this can be real even
    though the mean is (approximately) unchanged, and why it alone is
    nearly team-invariant at a given slot label.

    `has_bench_depth` is the real team-specific signal: whether this team's
    own bench carries at least one same-position player behind this slot's
    starter(s) -- computed the same way `sim.api.roster_view`'s
    `positional_concentration` already does. `bench_depth_relevant` is
    whether this slot's real position(s) are even ones a real roster is
    expected to carry bench depth at (`_BENCH_DEPTH_RELEVANT_POSITIONS` --
    false for a slot resolving to K or D/ST, the same exclusion
    `sim.api.waiver_intelligence_view`'s own `bench_depth_relevant` field
    already applies). `is_playoff_specific_weakness` requires
    `floor_ratio_delta` clearing the editorial threshold, AND
    `has_bench_depth` being false, AND `bench_depth_relevant` being true --
    see the module docstring.
    """

    slot_label: str
    regular_mean_points_per_week: float
    playoff_mean_points_per_week: float
    regular_floor_points_per_week: float
    playoff_floor_points_per_week: float
    regular_floor_ratio: float
    playoff_floor_ratio: float
    floor_ratio_delta: float
    has_bench_depth: bool
    bench_depth_relevant: bool
    # 1 = the league's strongest projected playoff-weeks scorer at this slot.
    league_rank: int
    league_team_count: int
    # 0-100; 100 = strongest in the league at this slot, matching league_rank.
    league_percentile: float
    is_playoff_specific_weakness: bool


@dataclass(frozen=True)
class PlayoffSeedOdds:
    """One team's simulated postseason outlook -- title/playoff/reached-final
    probabilities and `finish_distribution` are read straight off
    `SimulationResult`, unmodified (same numbers `GET /league/{id}/simulation`
    would report for the same seed/n_sims). `seed_probabilities[s]` is the
    probability this team ends the regular season holding seed `s` (0 = top
    seed), derived from `SimulationResult.seed_rank` via `np.bincount` -- a
    pure post-processing read of an existing output tensor."""

    team_id: int
    team_name: str
    title_probability: float
    playoff_probability: float
    reached_final_probability: float
    finish_distribution: tuple[float, ...]
    seed_probabilities: tuple[float, ...]
    # 1-indexed seed this team is assigned in the single projected bracket
    # (see PlayoffPlannerResult.bracket), or None if the maximum-weight
    # assignment did not select this team for any of the n_playoff_teams
    # slots -- a real, honest possibility for a team with low playoff odds.
    projected_seed: int | None


@dataclass(frozen=True)
class BracketMatchup:
    """One round-1 matchup in the single projected bracket, seeded from the
    maximum-weight assignment of teams to seed slots (see module docstring).
    Only round 1 is named with specific teams -- which two teams would meet
    in a later round depends on stochastic round-1 outcomes this projection
    intentionally does not resolve, so naming one would fabricate certainty
    the simulation doesn't have. `*_team_id`/`*_team_name` are None if no
    team was assigned that seed slot (possible only if n_playoff_teams
    exceeds the number of teams the maximum-weight assignment could fill,
    which cannot happen for a league where n_playoff_teams <= n_teams, but
    kept nullable rather than assumed)."""

    high_seed: int
    high_seed_team_id: int | None
    high_seed_team_name: str | None
    low_seed: int
    low_seed_team_id: int | None
    low_seed_team_name: str | None


@dataclass(frozen=True)
class TeamPlayoffPlan:
    team_id: int
    team_name: str
    slot_strengths: tuple[SlotPlayoffStrength, ...]
    # slot_label of the SlotPlayoffStrength _synthesize_recommendation used
    # to write `recommendation`, or None if this team has no evaluable
    # starting slots at all.
    weakest_slot: str | None
    # A real narrative synthesized server-side from this team's own
    # slot-strength numbers -- never a template filled with just the team
    # name (see _synthesize_recommendation).
    recommendation: str


@dataclass(frozen=True)
class PlayoffPlannerResult:
    league_id: int
    season_id: int
    n_regular_weeks: int
    n_playoff_rounds: int
    n_playoff_teams: int
    seed: int
    n_sims: int
    seeding: tuple[PlayoffSeedOdds, ...]
    bracket: tuple[BracketMatchup, ...]
    teams: tuple[TeamPlayoffPlan, ...]


def _synthesize_recommendation(
    team_name: str,
    weakest: SlotPlayoffStrength | None,
    is_real_weakness: bool,
    n_playoff_rounds: int,
) -> str:
    """A team-specific recommendation built entirely from that team's own
    already-computed `SlotPlayoffStrength` numbers -- see
    sim.api.draft_autopsy_view's `_synthesize_structural_finding` for the
    same "real narrative, not a template" precedent this mirrors, including
    an honest fallback sentence when nothing clears the weakness threshold.
    """
    if weakest is None:
        return f"{team_name} has no playoff-eligible starting slots to evaluate here."

    window = f"{n_playoff_rounds}-round" if n_playoff_rounds != 1 else "single-week"

    if is_real_weakness:
        return (
            f"Target {weakest.slot_label} now, on waivers or in a trade, before the rest of the "
            f"league notices. You have zero bench depth behind your {weakest.slot_label} starter"
            f"{'s' if weakest.slot_label in ('RB', 'WR') else ''} -- no in-house fallback if that "
            f"spot has a bad week. That matters far more in the playoffs than in the regular "
            f"season: over a full season, {team_name}'s {weakest.slot_label} group's bad-week "
            f"floor still delivers {weakest.regular_floor_ratio * 100:.0f}% of its average output, "
            f"but compressed into a {window} playoff bracket with no bench cushion, that floor "
            f"drops to just {weakest.playoff_floor_ratio * 100:.0f}% of average. It currently "
            f"ranks {weakest.league_rank} of {weakest.league_team_count} teams in projected "
            f"playoff-weeks scoring at {weakest.slot_label} -- add depth there before the teams "
            f"ahead of you do."
        )

    return (
        f"No position on {team_name}'s roster shows a real playoff-specific weakness: every "
        f"slot that gets riskier in a short {window} bracket already has same-position bench "
        f"depth behind it. The closest thing to a soft spot is {weakest.slot_label}, which ranks "
        f"{weakest.league_rank} of {weakest.league_team_count} teams in projected playoff-weeks "
        f"scoring there -- worth a depth add if a clean upgrade appears, but not urgent."
    )


def compute_playoff_planner(
    conn: psycopg.Connection[Any],
    league_id: int,
    season_id: int,
    *,
    n_sims: int = DEFAULT_N_SIMS,
) -> PlayoffPlannerResult:
    """Load an ingested league/season and build its Playoff Planner. Raises
    the same errors every other `sim.api` view module raises for the same
    conditions (`LeagueNotIngestedError` via `load_raw_payload`;
    `ingest.errors.RosterNotAvailableError` / `MissingProjectionError` via
    `build_team_params` for a league with no drafted, fully-projectable
    roster) -- propagated unchanged, translated to HTTP by the route
    handler."""
    raw = load_raw_payload(conn, league_id, season_id)
    return _compute_from_raw(raw, league_id, season_id, n_sims=n_sims)


def _compute_from_raw(
    raw: Mapping[str, Any], league_id: int, season_id: int, *, n_sims: int
) -> PlayoffPlannerResult:
    """The actual computation, factored out from `compute_playoff_planner` so
    it can be exercised directly against an in-memory fixture dict (no
    Postgres needed) -- same fast-unit-tests-plus-thin-integration-test split
    `sim.api.draft_autopsy_view` already established.
    """
    scoring_table = parse_scoring_table(raw)
    player_pool, _skipped = parse_player_pool(raw, scoring_table)
    position_cv = fit_position_cv(raw)
    players_by_id: dict[int, PlayerParams] = derive_player_params_pool(player_pool, position_cv)
    # Position metadata (defaultPositionId), for the bench-depth check below
    # -- players_by_id only carries mean/sd/availability, same split
    # sim.api.roster_view already relies on.
    projections_by_id = {p.player_id: p for p in player_pool}

    teams = parse_teams(raw)
    schedule_settings = raw["settings"]["scheduleSettings"]
    n_regular_weeks = int(schedule_settings["matchupPeriodCount"])
    n_playoff_teams = int(schedule_settings["playoffTeamCount"])
    schedule = parse_schedule(raw, teams, n_regular_weeks)

    team_params = tuple(build_team_params(raw, t.team_id, players_by_id) for t in teams)
    league = LeagueParams(
        teams=team_params, schedule=schedule.pairings, n_playoff_teams=n_playoff_teams
    )
    n_teams = league.n_teams
    n_playoff_rounds = int(np.ceil(np.log2(_next_power_of_two(n_playoff_teams))))

    # One seed, deterministically derived from (league_id, season_id) -- the
    # exact same formula sim.api.precompute uses for the cached
    # /league/{id}/simulation endpoint (sim.api.seeds.precompute_seed), so
    # this live-computed-but-reproducible view is consistent with that one
    # for the same league/season rather than drawing an unrelated seed.
    seed = precompute_seed(league_id, season_id)
    rng = np.random.default_rng(seed)

    result = simulate_seasons(league, n_sims=n_sims, rng=rng)

    # --- Seeding odds and the single projected bracket ---------------------
    # (n_teams, n_playoff_teams): P(team t ends the regular season holding
    # seed s), read straight off SimulationResult.seed_rank.
    seed_prob_matrix = np.stack(
        [
            (np.bincount(result.seed_rank[:, t], minlength=n_teams) / n_sims)[:n_playoff_teams]
            for t in range(n_teams)
        ]
    )

    # Maximum-weight assignment of teams to seed slots -- the single most
    # probable bracket, not a per-simulation replay. linear_sum_assignment
    # minimizes cost, so negate to maximize probability; rectangular
    # (n_teams > n_playoff_teams for every league this ever runs against)
    # returns exactly n_playoff_teams row/col pairs.
    row_ind, col_ind = linear_sum_assignment(-seed_prob_matrix)
    projected_seed_of_team_idx: dict[int, int] = dict(zip(row_ind, col_ind, strict=True))
    team_idx_of_seed_idx: dict[int, int] = {
        seed_idx: team_idx for team_idx, seed_idx in zip(row_ind, col_ind, strict=True)
    }

    bracket: list[BracketMatchup] = []
    for high_idx, low_idx in bracket_pairings(n_playoff_teams):
        high_team_idx = team_idx_of_seed_idx.get(high_idx)
        low_team_idx = team_idx_of_seed_idx.get(low_idx)
        bracket.append(
            BracketMatchup(
                high_seed=high_idx + 1,
                high_seed_team_id=team_params[high_team_idx].team_id
                if high_team_idx is not None
                else None,
                high_seed_team_name=team_params[high_team_idx].name
                if high_team_idx is not None
                else None,
                low_seed=low_idx + 1,
                low_seed_team_id=team_params[low_team_idx].team_id
                if low_team_idx is not None
                else None,
                low_seed_team_name=team_params[low_team_idx].name
                if low_team_idx is not None
                else None,
            )
        )

    seeding: list[PlayoffSeedOdds] = []
    for t in range(n_teams):
        seeding.append(
            PlayoffSeedOdds(
                team_id=result.team_ids[t],
                team_name=result.team_names[t],
                title_probability=float(result.won_title[t]),
                playoff_probability=float(result.made_playoffs[t]),
                reached_final_probability=float(result.reached_final[t]),
                finish_distribution=tuple(float(x) for x in result.finish_distribution[t]),
                seed_probabilities=tuple(float(x) for x in seed_prob_matrix[t]),
                projected_seed=(
                    projected_seed_of_team_idx[t] + 1 if t in projected_seed_of_team_idx else None
                ),
            )
        )

    # --- Per-roster-slot playoff-window strength ----------------------------
    slot_requirements = starting_slot_counts(raw["settings"]["rosterSettings"]["lineupSlotCounts"])
    league_slot_labels = tuple(SLOT_NAMES.get(slot_id, str(slot_id)) for slot_id in slot_requirements)

    raw_teams_by_id = {t["id"]: t for t in raw["teams"]}
    # Same deterministic seed, a fresh Generator: this is a second, clearly-
    # documented consumer of the identical fitted Gamma(mean, sd) x
    # availability model (sim.engine._sample_player_weeks), not a second
    # simulation of season outcomes -- nothing here decides a win, a seed, or
    # a title, only how a team's own points are expected to split across its
    # starting slots for the playoff-length window. Kept as its own rng
    # instance (not the one already advanced by simulate_seasons() above) so
    # this module's two computations stay independently reproducible and
    # order-independent of each other.
    slot_rng = np.random.default_rng(seed)

    # team_id -> {slot_label: (regular_mean, playoff_mean, regular_floor,
    # playoff_floor, regular_floor_ratio, playoff_floor_ratio, has_bench_depth,
    # bench_depth_relevant)}
    per_team_slot_stats: dict[
        int, dict[str, tuple[float, float, float, float, float, float, bool, bool]]
    ] = {}

    for team in team_params:
        raw_team = raw_teams_by_id[team.team_id]
        entries = raw_team.get("roster", {}).get("entries", [])

        # Same-position bench-depth count for this team -- the identical
        # rule sim.api.roster_view._positional_concentration already uses
        # (count bench/IR entries by their own defaultPositionId label), not
        # by slot_label, so a bench RB backs up an RB or FLEX-RB starter
        # alike. This is the real team-specific signal (see module
        # docstring); floor_ratio_delta below is nearly team-invariant.
        bench_position_counts: Counter[str] = Counter(
            DEFAULT_POSITION_NAMES.get(
                projections_by_id[e["playerId"]].default_position_id, "?"
            )
            for e in entries
            if e["lineupSlotId"] in NON_STARTING_SLOTS and e["playerId"] in projections_by_id
        )

        ordered_players: list[PlayerParams] = []
        slot_labels: list[str] = []
        real_positions: list[str] = []
        for entry in entries:
            slot_id = entry["lineupSlotId"]
            if slot_id in NON_STARTING_SLOTS:
                continue
            player_id = entry["playerId"]
            ordered_players.append(players_by_id[player_id])
            slot_labels.append(SLOT_NAMES.get(slot_id, str(slot_id)))
            real_positions.append(
                DEFAULT_POSITION_NAMES.get(projections_by_id[player_id].default_position_id, "?")
            )

        sampled = _sample_player_weeks(
            tuple(ordered_players),
            n_sims=n_sims,
            n_weeks=n_regular_weeks + n_playoff_rounds,
            rng=slot_rng,
        )
        regular = sampled[:, :n_regular_weeks, :]
        playoff = sampled[:, n_regular_weeks:, :]

        slot_stats: dict[str, tuple[float, float, float, float, float, float, bool, bool]] = {}
        for slot_label in league_slot_labels:
            idxs = [i for i, label in enumerate(slot_labels) if label == slot_label]
            if not idxs:
                continue
            regular_ppw = regular[:, :, idxs].sum(axis=(1, 2)) / n_regular_weeks
            playoff_ppw = playoff[:, :, idxs].sum(axis=(1, 2)) / n_playoff_rounds

            regular_mean = float(regular_ppw.mean())
            playoff_mean = float(playoff_ppw.mean())
            regular_floor = float(np.percentile(regular_ppw, _FLOOR_PERCENTILE_PCT))
            playoff_floor = float(np.percentile(playoff_ppw, _FLOOR_PERCENTILE_PCT))
            regular_floor_ratio = regular_floor / regular_mean if regular_mean > 0 else 0.0
            playoff_floor_ratio = playoff_floor / playoff_mean if playoff_mean > 0 else 0.0

            # Real position(s) this slot's actual starter(s) play -- FLEX
            # resolves to whichever real position occupies it for this
            # team, so bench depth is checked at the position that actually
            # matters, not at the literal "FLEX" label.
            slot_real_positions = {real_positions[i] for i in idxs}
            has_bench_depth = any(bench_position_counts[pos] > 0 for pos in slot_real_positions)
            # False for a slot resolving to K or D/ST -- see
            # _BENCH_DEPTH_RELEVANT_POSITIONS.
            bench_depth_relevant = any(
                pos in _BENCH_DEPTH_RELEVANT_POSITIONS for pos in slot_real_positions
            )

            slot_stats[slot_label] = (
                regular_mean,
                playoff_mean,
                regular_floor,
                playoff_floor,
                regular_floor_ratio,
                playoff_floor_ratio,
                has_bench_depth,
                bench_depth_relevant,
            )
        per_team_slot_stats[team.team_id] = slot_stats

    # League-wide rank/percentile per slot, by playoff-window mean points --
    # "how does my slot's playoff-weeks scoring compare to the field of
    # realistic bracket opponents" (see module docstring's data-gap
    # resolution).
    league_playoff_means: dict[str, list[tuple[int, float]]] = {label: [] for label in league_slot_labels}
    for team_id, stats in per_team_slot_stats.items():
        for slot_label, values in stats.items():
            league_playoff_means[slot_label].append((team_id, values[1]))
    league_rank_by_slot_team: dict[tuple[str, int], tuple[int, int]] = {}
    for slot_label, entries in league_playoff_means.items():
        ranked = sorted(entries, key=lambda e: -e[1])
        team_count = len(ranked)
        for rank, (team_id, _mean) in enumerate(ranked, start=1):
            league_rank_by_slot_team[(slot_label, team_id)] = (rank, team_count)

    team_plans: list[TeamPlayoffPlan] = []
    for team in team_params:
        slot_stats = per_team_slot_stats[team.team_id]
        slot_strengths: list[SlotPlayoffStrength] = []
        for slot_label in league_slot_labels:
            if slot_label not in slot_stats:
                continue
            (
                regular_mean,
                playoff_mean,
                regular_floor,
                playoff_floor,
                regular_floor_ratio,
                playoff_floor_ratio,
                has_bench_depth,
                bench_depth_relevant,
            ) = slot_stats[slot_label]
            rank, team_count = league_rank_by_slot_team[(slot_label, team.team_id)]
            percentile = 100.0 * (team_count - rank) / (team_count - 1) if team_count > 1 else 100.0
            delta = regular_floor_ratio - playoff_floor_ratio

            slot_strengths.append(
                SlotPlayoffStrength(
                    slot_label=slot_label,
                    regular_mean_points_per_week=regular_mean,
                    playoff_mean_points_per_week=playoff_mean,
                    regular_floor_points_per_week=regular_floor,
                    playoff_floor_points_per_week=playoff_floor,
                    regular_floor_ratio=regular_floor_ratio,
                    playoff_floor_ratio=playoff_floor_ratio,
                    floor_ratio_delta=delta,
                    has_bench_depth=has_bench_depth,
                    bench_depth_relevant=bench_depth_relevant,
                    league_rank=rank,
                    league_team_count=team_count,
                    league_percentile=percentile,
                    is_playoff_specific_weakness=(
                        delta >= _WEAKNESS_DELTA_THRESHOLD
                        and not has_bench_depth
                        and bench_depth_relevant
                    ),
                )
            )

        weak_candidates = [s for s in slot_strengths if s.is_playoff_specific_weakness]
        if weak_candidates:
            weakest: SlotPlayoffStrength | None = max(weak_candidates, key=lambda s: s.floor_ratio_delta)
            is_real_weakness = True
        elif slot_strengths:
            weakest = min(slot_strengths, key=lambda s: s.league_percentile)
            is_real_weakness = False
        else:
            weakest = None
            is_real_weakness = False

        team_plans.append(
            TeamPlayoffPlan(
                team_id=team.team_id,
                team_name=team.name,
                slot_strengths=tuple(slot_strengths),
                weakest_slot=weakest.slot_label if weakest else None,
                recommendation=_synthesize_recommendation(
                    team.name, weakest, is_real_weakness, n_playoff_rounds
                ),
            )
        )

    return PlayoffPlannerResult(
        league_id=league_id,
        season_id=season_id,
        n_regular_weeks=n_regular_weeks,
        n_playoff_rounds=n_playoff_rounds,
        n_playoff_teams=n_playoff_teams,
        seed=seed,
        n_sims=n_sims,
        seeding=tuple(seeding),
        bracket=tuple(bracket),
        teams=tuple(team_plans),
    )
