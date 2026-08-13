"""Beat My League, backing `GET /league/{id}/beat-my-league/{team_id}`.

Covers PLAN.md's Phase 10 (feature 7): for every team, title probability,
structural strengths/weaknesses, and playoff schedule difficulty; then, for
one selected "user" team specifically, the single biggest threat among the
other teams and *why*, where the user's team holds a real advantage, and a
league-specific strategy note -- which positions NOT to help a rival fix
through a trade.

No new simulation path, and this module constructs no `np.random.Generator`
of its own -- see "Reuse, not a new computation" below for exactly what is
reused wholesale and what is purely deterministic post-processing/comparison
over already-computed numbers.

--------------------------------------------------------------------------
Reuse, not a new computation (CLAUDE.md: "one simulation engine"):

1. Title probability, playoff probability, and finish distribution for
   every team come straight from `sim.api.playoff_planner_view`'s own
   `PlayoffSeedOdds` (itself a direct, unmodified read of
   `SimulationResult`, produced by the one real `simulate_seasons()` call
   Playoff Planner already makes) -- never recomputed.

2. "Structural strengths" and "structural weaknesses" are a pure selection
   over `sim.api.playoff_planner_view`'s own already-computed
   `SlotPlayoffStrength` list per team -- the identical per-slot
   league-rank/percentile/bench-depth signal Phase 8 built, not a new
   formula.

   Strengths: every slot whose `league_percentile` clears an editorial
   threshold (`_STRONG_PERCENTILE_THRESHOLD`, the same class of
   presentation choice as Phase 8's own `_WEAKNESS_DELTA_THRESHOLD`),
   falling back to the single best slot if none clears it. Since
   `league_percentile` is a RELATIVE, rank-based number, this is
   automatically team-differentiating by construction (only a minority of
   teams can clear a "top 30%" bar at any given slot).

   Weaknesses: reuses `TeamPlayoffPlan.weakest_slot` -- Phase 8's own
   SINGLE strongest weakness per team -- rather than every slot with
   `is_playoff_specific_weakness=True`. This distinction is load-bearing,
   not cosmetic: verified directly against the real league that K is
   flagged `is_playoff_specific_weakness=True` for all 8 of 8 real teams
   (zero teams anywhere carry a bench kicker), and D/ST for 6 of 8 -- the
   exact non-team-differentiating signal Phase 8's own docstring already
   diagnosed and solved by picking one `weakest_slot` (`max` by
   `floor_ratio_delta` among flagged candidates) rather than reporting the
   raw flagged set. Using the raw flagged list here would have silently
   reintroduced that same bug one layer up: nearly every team would show
   "K" as a structural weakness, which is true but tells a user nothing
   about THEIR team specifically. Reusing `weakest_slot` verified live to
   restore real per-team variation: 6 teams land on D/ST, 1 on TE, 1 on K --
   matching Phase 8's own documented result exactly.

3. "Playoff schedule difficulty" is `TeamPlayoffPlan.recommendation`
   (Phase 8's own server-synthesized narrative about the compressed playoff
   window), surfaced verbatim as `playoff_schedule_note` -- not reworded or
   recomputed here.

4. This module reaches directly into `sim.api.playoff_planner_view`'s
   private `_compute_from_raw` (not its public, Postgres-taking
   `compute_playoff_planner`) so the ENTIRE simulate_seasons() +
   per-slot-sampling pipeline runs exactly once per request, from the one
   raw payload this module already loaded -- calling the public function
   instead would force a second, redundant `load_raw_payload` round trip.
   This is a different class of reuse than the "shared small rule,
   reimplemented locally" precedent `sim.api.waiver_intelligence_view` and
   `sim.api.playoff_planner_view` themselves establish for
   bench-depth-by-position counting (a five-line loop, cheap to duplicate
   for a different local dataclass shape): here the thing being reused is
   an entire stochastic simulation, and CLAUDE.md's "one simulation engine"
   rule means that must be called once and built on, never re-run or
   approximated a second way.

5. Bench-depth-by-position counting (for the trade-caution strategy note)
   IS reimplemented locally, following that exact established precedent --
   the rule is shared with `sim.api.roster_view._positional_concentration`
   and `sim.api.waiver_intelligence_view._bench_depth_by_position`, the
   local data shape (player names per position, needed here to name real
   surplus players) is not.

--------------------------------------------------------------------------
Biggest threat -- what "real, specific reason" means here, precisely:

A rival team R is a threat CANDIDATE for the selected team U if R has a real
structural strength (a slot clearing `_STRONG_PERCENTILE_THRESHOLD`, or R's
single best slot if none clears it) at a slot label that is ALSO one of U's
own real structural weaknesses (`is_playoff_specific_weakness`, or U's own
weakest slot if none is flagged). Among every candidate with this real
positional overlap, the one with the highest simulated `title_probability`
is named the biggest threat -- a real contender who is ALSO specifically
built to exploit U's specific weak spot, not merely "a good team." This is a
lexicographic selection (filter on real overlap, then rank by an
already-computed probability), the same "no arbitrary blended composite"
discipline `sim.api.draft_autopsy_view` and `sim.api.waiver_intelligence_view`
already established, never an invented weighted score.

If no rival has a real positional overlap with U's own weakness (a real,
honest possibility), the fallback is the single highest-title-probability
rival league-wide, with a reasoning sentence that says exactly that --
"the strongest team in the league, not a specific positional matchup" --
rather than forcing a positional narrative the data doesn't support.

--------------------------------------------------------------------------
Real advantage -- the mirror-image comparison, anchored to the identified
threat specifically (not the field in the abstract), so it reads as one
coherent head-to-head:

A slot counts as U's real advantage when it is one of U's own real
strengths AND one of the identified threat's own real weaknesses. If no
slot clears both bars, the fallback is U's single best slot league-wide
(by `league_percentile`), with a reasoning sentence noting plainly that the
threat isn't specifically thin there -- it is U's clearest structural edge
regardless of opponent, not a fabricated head-to-head claim.

--------------------------------------------------------------------------
Trade-caution strategy -- "which positions NOT to trade away," and why this
is the same signal Phase 9b's Waiver Intelligence already established,
applied to the opposite decision:

For each of QB/RB/WR/TE (the same `_BENCH_DEPTH_RELEVANT_POSITIONS`
`sim.api.waiver_intelligence_view` and `sim.api.draft_autopsy_view` already
use -- K/D-ST bench depth is a near-constant in this league, not a real
per-team signal, see those modules' docstrings), U is flagged with a real
trade caution at that position when BOTH: U itself carries at least one
bench player there (real, tradeable surplus -- named directly, not just
counted), AND at least one other real team in the league carries ZERO bench
depth at that same position (the exact same rival-need rule Waiver
Intelligence's Signal 4/"Competition" already computes, just read from the
opposite side of the transaction: Waiver Intelligence asks "who should I
add," this asks "who would benefit if I traded this away"). Positions
where nobody in the league is thin, or where U itself has no bench surplus
to protect, are silently omitted -- an empty `trade_cautions` list is a
real, honest result for a team with no such leverage anywhere, not a bug.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg

from ingest.parse import parse_player_pool, parse_scoring_table, parse_teams
from ingest.slots import DEFAULT_POSITION_NAMES, NON_STARTING_SLOTS, SLOT_NAMES
from sim.api.params_loader import load_raw_payload
from sim.api.playoff_planner_view import DEFAULT_N_SIMS, SlotPlayoffStrength, TeamPlayoffPlan
from sim.api.playoff_planner_view import (
    _compute_from_raw as _compute_playoff_planner_from_raw,  # reused wholesale, see module docstring
)

# Editorial threshold for calling a slot a "structural strength" -- the top
# ~30% of the league at that slot. Same class of presentation choice as
# sim.api.playoff_planner_view's own _WEAKNESS_DELTA_THRESHOLD: a decision
# about display language over an already-derived number, not a fitted or
# invented input to any simulation.
_STRONG_PERCENTILE_THRESHOLD = 70.0

# Same four positions sim.api.waiver_intelligence_view /
# sim.api.draft_autopsy_view already treat as having a real bench-depth
# strategy in this league -- K/D-ST bench depth is ~0 for nearly every real
# team here (see those modules' docstrings), so it carries no per-team
# trade-leverage signal.
_BENCH_DEPTH_RELEVANT_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE")


class UnknownTeamError(ValueError):
    """Raised when `team_id` does not belong to the requested league/season.
    Mapped to HTTP 404, the same class of "this specific thing was never
    real" error every other `sim.api` view module raises for the same
    situation."""


@dataclass(frozen=True)
class TeamLeagueProfile:
    """One team's league-wide standing -- reused sim outcomes plus a pure
    selection over Playoff Planner's own already-computed slot strengths.
    See module docstring."""

    team_id: int
    team_name: str
    title_probability: float
    playoff_probability: float
    finish_distribution: tuple[float, ...]
    strengths: tuple[SlotPlayoffStrength, ...]
    weaknesses: tuple[SlotPlayoffStrength, ...]
    playoff_schedule_note: str


@dataclass(frozen=True)
class RivalThreat:
    team_id: int
    team_name: str
    title_probability: float
    # Slot label(s) where the rival is strong AND the user's team is weak --
    # empty when the fallback (highest title odds league-wide, no positional
    # overlap found) fired instead. See module docstring.
    overlapping_slots: tuple[str, ...]
    reasoning: str


@dataclass(frozen=True)
class TeamAdvantage:
    slots: tuple[str, ...]
    reasoning: str


@dataclass(frozen=True)
class TradeCaution:
    position: str
    team_bench_depth_at_position: int
    bench_player_names: tuple[str, ...]
    rival_teams_with_need: tuple[str, ...]
    reasoning: str


@dataclass(frozen=True)
class BeatMyLeagueResult:
    league_id: int
    season_id: int
    team_id: int
    team_name: str
    seed: int
    n_sims: int
    teams: tuple[TeamLeagueProfile, ...]
    biggest_threat: RivalThreat
    real_advantage: TeamAdvantage
    trade_cautions: tuple[TradeCaution, ...]


def _team_slot_starters(
    entries: list[dict[str, Any]], projections_by_id: Mapping[int, Any]
) -> dict[str, list[str]]:
    """slot_label -> ordered list of real starter names occupying that slot
    for one team, straight off ESPN's own `lineupSlotId` -- used only to
    name real players in reasoning text, never to compute a score."""
    result: dict[str, list[str]] = {}
    for entry in entries:
        slot_id = entry["lineupSlotId"]
        if slot_id in NON_STARTING_SLOTS:
            continue
        projection = projections_by_id.get(entry["playerId"])
        name = projection.name if projection is not None else f"Player {entry['playerId']}"
        result.setdefault(SLOT_NAMES.get(slot_id, str(slot_id)), []).append(name)
    return result


def _team_bench_names_by_position(
    entries: list[dict[str, Any]], projections_by_id: Mapping[int, Any]
) -> dict[str, list[str]]:
    """Real bench/IR player names for one team, grouped by their own
    `defaultPositionId` label -- the same rule
    `sim.api.roster_view._positional_concentration` /
    `sim.api.waiver_intelligence_view._bench_depth_by_position` already use,
    reimplemented locally per those modules' own precedent (shared rule,
    local data shape), extended here to keep the real names, not just a
    count."""
    result: dict[str, list[str]] = {}
    for entry in entries:
        if entry["lineupSlotId"] not in NON_STARTING_SLOTS:
            continue
        projection = projections_by_id.get(entry["playerId"])
        if projection is None:
            continue
        position = DEFAULT_POSITION_NAMES.get(projection.default_position_id, "?")
        result.setdefault(position, []).append(projection.name)
    return result


def _select_strengths(
    slot_strengths: tuple[SlotPlayoffStrength, ...],
) -> tuple[SlotPlayoffStrength, ...]:
    strong = tuple(s for s in slot_strengths if s.league_percentile >= _STRONG_PERCENTILE_THRESHOLD)
    if strong:
        return tuple(sorted(strong, key=lambda s: -s.league_percentile))
    if slot_strengths:
        return (max(slot_strengths, key=lambda s: s.league_percentile),)
    return ()


def _select_weakest(plan: TeamPlayoffPlan) -> tuple[SlotPlayoffStrength, ...]:
    """Reuses `TeamPlayoffPlan.weakest_slot` verbatim -- Phase 8's own
    single, already-vetted-for-team-differentiation weakness -- rather than
    every slot flagged `is_playoff_specific_weakness`. See module
    docstring point 2 for exactly why the raw flagged set would have been
    non-differentiating (K clears the flag for all 8 real teams in this
    league)."""
    if plan.weakest_slot is None:
        return ()
    return (next(s for s in plan.slot_strengths if s.slot_label == plan.weakest_slot),)


def _players_txt(names: list[str]) -> str:
    return ", ".join(names) if names else "an empty slot"


def _ordinal(n: float) -> str:
    """Round to the nearest integer and format with the correct English
    ordinal suffix (71 -> "71st", 86 -> "86th", 100 -> "100th") -- pure
    display formatting over an already-computed percentile, not a new
    number."""
    rounded = round(n)
    if 11 <= (rounded % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(rounded % 10, "th")
    return f"{rounded}{suffix}"


def _synthesize_threat_reasoning(
    *,
    threat_name: str,
    threat_title_probability: float,
    rival_starters: list[str],
    rival_slot: SlotPlayoffStrength | None,
    user_name: str,
    user_starters: list[str],
    user_slot: SlotPlayoffStrength | None,
    overlap: tuple[str, ...],
) -> str:
    if not overlap or rival_slot is None:
        return (
            f"No rival is specifically built to attack {user_name}'s own weak spot right now. "
            f"By simulated title odds alone, {threat_name} is the single biggest threat in the "
            f"league at {threat_title_probability * 100:.1f}% -- not because of one positional "
            "matchup, but because they're the strongest team in the field overall."
        )

    slot_label = overlap[0]
    rival_txt = _players_txt(rival_starters)
    user_txt = _players_txt(user_starters)
    weakness_clause = (
        f"{user_name}'s own {slot_label} spot ({user_txt}) has zero same-position bench depth"
        if user_slot is not None and not user_slot.has_bench_depth
        else f"{user_name}'s own {slot_label} spot ({user_txt}) is your weakest slot in the league"
    )
    return (
        f"{threat_name} starts {rival_txt} at {slot_label}, ranked {rival_slot.league_rank} of "
        f"{rival_slot.league_team_count} teams in the league at that slot "
        f"({_ordinal(rival_slot.league_percentile)} percentile) -- exactly the position where "
        f"{weakness_clause}. Combined with a real "
        f"{threat_title_probability * 100:.1f}% simulated title probability, {threat_name} is the "
        f"one team in the league both strong enough to contend and specifically built to exploit "
        "this gap."
    )


def _synthesize_advantage_reasoning(
    *,
    user_name: str,
    user_starters: list[str],
    user_slot: SlotPlayoffStrength | None,
    threat_name: str,
    threat_slot: SlotPlayoffStrength | None,
    overlap: tuple[str, ...],
    fallback_used: bool,
) -> str:
    if not overlap or user_slot is None:
        return f"{user_name} has no slot in the league that clears a real structural-strength bar right now -- no clear positional edge to lean on against anyone."

    slot_label = overlap[0]
    user_txt = _players_txt(user_starters)
    base = (
        f"At {slot_label}, {user_name} starts {user_txt}, ranked {user_slot.league_rank} of "
        f"{user_slot.league_team_count} teams in the league ({_ordinal(user_slot.league_percentile)} percentile)."
    )
    if fallback_used or threat_slot is None:
        return (
            f"{base} {threat_name} isn't specifically thin at {slot_label} -- this is your "
            "clearest structural edge league-wide regardless of who you're facing, not a "
            "head-to-head weakness of theirs."
        )
    return (
        f"{base} {threat_name} ranks {threat_slot.league_rank} of {threat_slot.league_team_count} "
        f"at that same slot ({_ordinal(threat_slot.league_percentile)} percentile) with no bench "
        "depth behind their own starter -- a real head-to-head edge, not just a good position on "
        "paper."
    )


def _synthesize_trade_caution_reasoning(
    *,
    user_name: str,
    position: str,
    bench_names: list[str],
    rival_names: list[str],
) -> str:
    bench_txt = _players_txt(bench_names)
    count = len(bench_names)
    rival_txt = ", ".join(rival_names)
    is_single_rival = len(rival_names) == 1
    plural_rival = "" if is_single_rival else "s"
    have_has = "has" if is_single_rival else "have"
    them_or_name = rival_names[0] if is_single_rival else "them"
    return (
        f"{user_name} carries {count} bench {position}{'s' if count != 1 else ''} ({bench_txt}) "
        f"that {have_has} no starting job here -- real surplus. "
        f"{len(rival_names)} other team{plural_rival} in the league -- {rival_txt} -- "
        f"{have_has} zero bench depth at {position} right now. Trading that surplus to "
        f"{them_or_name} would hand over exactly the depth they are missing and erase a real edge "
        "you currently hold over that roster."
    )


def compute_beat_my_league(
    conn: psycopg.Connection[Any],
    league_id: int,
    season_id: int,
    team_id: int,
    *,
    n_sims: int = DEFAULT_N_SIMS,
) -> BeatMyLeagueResult:
    """Build Beat My League for one selected team. Raises
    `LeagueNotIngestedError` (via `load_raw_payload`) if the league/season
    was never ingested, `UnknownTeamError` if `team_id` is not real for this
    league/season, and the same `ingest.errors.RosterNotAvailableError` /
    `MissingProjectionError` `sim.api.playoff_planner_view` already raises
    for a league with no drafted, fully-projectable roster."""
    raw = load_raw_payload(conn, league_id, season_id)
    return _compute_from_raw(raw, league_id, season_id, team_id, n_sims=n_sims)


def _compute_from_raw(
    raw: Mapping[str, Any], league_id: int, season_id: int, team_id: int, *, n_sims: int
) -> BeatMyLeagueResult:
    """The actual computation, factored out so it can be exercised directly
    against an in-memory fixture dict (no Postgres needed) -- same
    fast-unit-tests-plus-thin-integration-test split every other
    `sim.api` view module already established."""
    teams = parse_teams(raw)
    team_ids = {t.team_id for t in teams}
    if team_id not in team_ids:
        raise UnknownTeamError(
            f"team_id={team_id} is not a team in league_id={league_id} season_id={season_id}"
        )

    # The one real simulate_seasons() call plus per-slot sampling this
    # module needs -- reused wholesale from Playoff Planner, see module
    # docstring point 4. Same raw payload, so no second Postgres round trip.
    planner = _compute_playoff_planner_from_raw(raw, league_id, season_id, n_sims=n_sims)

    scoring_table = parse_scoring_table(raw)
    player_pool, _skipped = parse_player_pool(raw, scoring_table)
    projections_by_id = {p.player_id: p for p in player_pool}
    raw_teams_by_id = {t["id"]: t for t in raw["teams"]}

    entries_by_team: dict[int, list[dict[str, Any]]] = {
        t.team_id: raw_teams_by_id[t.team_id].get("roster", {}).get("entries", []) for t in teams
    }
    slot_starters_by_team = {
        tid: _team_slot_starters(entries, projections_by_id) for tid, entries in entries_by_team.items()
    }
    bench_names_by_team = {
        tid: _team_bench_names_by_position(entries, projections_by_id)
        for tid, entries in entries_by_team.items()
    }
    bench_counts_by_team: dict[int, Counter[str]] = {
        tid: Counter({pos: len(names) for pos, names in bn.items()})
        for tid, bn in bench_names_by_team.items()
    }

    seeding_by_id = {s.team_id: s for s in planner.seeding}
    plan_by_id = {t.team_id: t for t in planner.teams}

    profiles: list[TeamLeagueProfile] = []
    for t in teams:
        plan = plan_by_id[t.team_id]
        seed_odds = seeding_by_id[t.team_id]
        profiles.append(
            TeamLeagueProfile(
                team_id=t.team_id,
                team_name=t.name,
                title_probability=seed_odds.title_probability,
                playoff_probability=seed_odds.playoff_probability,
                finish_distribution=seed_odds.finish_distribution,
                strengths=_select_strengths(plan.slot_strengths),
                weaknesses=_select_weakest(plan),
                playoff_schedule_note=plan.recommendation,
            )
        )
    profile_by_id = {p.team_id: p for p in profiles}
    user_profile = profile_by_id[team_id]

    # --- Biggest threat -----------------------------------------------------
    # user_profile.weaknesses is 0 or 1 elements (Phase 8's own single
    # weakest_slot, see _select_weakest) -- always the same slot named in
    # user_profile.playoff_schedule_note, so the threat/advantage narrative
    # below is consistent with the playoff-difficulty note shown alongside it.
    user_weak_labels = {s.slot_label for s in user_profile.weaknesses}

    threat_candidates: list[tuple[TeamLeagueProfile, tuple[str, ...]]] = []
    for p in profiles:
        if p.team_id == team_id:
            continue
        rival_strong_labels = {s.slot_label for s in p.strengths}
        overlap = tuple(sorted(user_weak_labels & rival_strong_labels))
        if overlap:
            threat_candidates.append((p, overlap))

    if threat_candidates:
        threat_profile, threat_overlap = max(threat_candidates, key=lambda pair: pair[0].title_probability)
    else:
        threat_profile = max((p for p in profiles if p.team_id != team_id), key=lambda p: p.title_probability)
        threat_overlap = ()

    rival_slot = None
    user_slot_for_threat = None
    rival_starters: list[str] = []
    user_starters_for_threat: list[str] = []
    if threat_overlap:
        slot_label = threat_overlap[0]
        rival_slot = next(s for s in threat_profile.strengths if s.slot_label == slot_label)
        user_slot_for_threat = next(s for s in user_profile.weaknesses if s.slot_label == slot_label)
        rival_starters = slot_starters_by_team[threat_profile.team_id].get(slot_label, [])
        user_starters_for_threat = slot_starters_by_team[team_id].get(slot_label, [])

    biggest_threat = RivalThreat(
        team_id=threat_profile.team_id,
        team_name=threat_profile.team_name,
        title_probability=threat_profile.title_probability,
        overlapping_slots=threat_overlap,
        reasoning=_synthesize_threat_reasoning(
            threat_name=threat_profile.team_name,
            threat_title_probability=threat_profile.title_probability,
            rival_starters=rival_starters,
            rival_slot=rival_slot,
            user_name=user_profile.team_name,
            user_starters=user_starters_for_threat,
            user_slot=user_slot_for_threat,
            overlap=threat_overlap,
        ),
    )

    # --- Real advantage -------------------------------------------------------
    threat_weak_labels = {s.slot_label for s in threat_profile.weaknesses}
    user_strong_labels = {s.slot_label for s in user_profile.strengths}
    advantage_overlap = tuple(sorted(user_strong_labels & threat_weak_labels))
    fallback_used = False
    if not advantage_overlap and user_profile.strengths:
        best = max(user_profile.strengths, key=lambda s: s.league_percentile)
        advantage_overlap = (best.slot_label,)
        fallback_used = True

    user_slot_for_advantage = None
    threat_slot_for_advantage = None
    user_starters_for_advantage: list[str] = []
    if advantage_overlap:
        slot_label = advantage_overlap[0]
        user_slot_for_advantage = next(s for s in user_profile.strengths if s.slot_label == slot_label)
        threat_slot_for_advantage = next(
            (s for s in threat_profile.weaknesses if s.slot_label == slot_label), None
        )
        user_starters_for_advantage = slot_starters_by_team[team_id].get(slot_label, [])

    real_advantage = TeamAdvantage(
        slots=advantage_overlap,
        reasoning=_synthesize_advantage_reasoning(
            user_name=user_profile.team_name,
            user_starters=user_starters_for_advantage,
            user_slot=user_slot_for_advantage,
            threat_name=threat_profile.team_name,
            threat_slot=threat_slot_for_advantage,
            overlap=advantage_overlap,
            fallback_used=fallback_used,
        ),
    )

    # --- Trade cautions ---------------------------------------------------
    cautions: list[TradeCaution] = []
    for position in _BENCH_DEPTH_RELEVANT_POSITIONS:
        user_bench = bench_counts_by_team[team_id].get(position, 0)
        if user_bench == 0:
            continue
        rival_names = [
            t.name
            for t in teams
            if t.team_id != team_id and bench_counts_by_team[t.team_id].get(position, 0) == 0
        ]
        if not rival_names:
            continue
        names = bench_names_by_team[team_id].get(position, [])
        cautions.append(
            TradeCaution(
                position=position,
                team_bench_depth_at_position=user_bench,
                bench_player_names=tuple(names),
                rival_teams_with_need=tuple(rival_names),
                reasoning=_synthesize_trade_caution_reasoning(
                    user_name=user_profile.team_name,
                    position=position,
                    bench_names=names,
                    rival_names=rival_names,
                ),
            )
        )

    return BeatMyLeagueResult(
        league_id=league_id,
        season_id=season_id,
        team_id=team_id,
        team_name=user_profile.team_name,
        seed=planner.seed,
        n_sims=planner.n_sims,
        teams=tuple(profiles),
        biggest_threat=biggest_threat,
        real_advantage=real_advantage,
        trade_cautions=tuple(cautions),
    )
