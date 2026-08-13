"""Power Ranking Roast, backing `GET /league/{id}/power-ranking-roast`.

Covers the "power ranking roast" half of PLAN.md's Phase 12 (feature 16)
ONLY. Weekly awards (team of the week, worst start/sit, luckiest/unluckiest,
biggest riser/faller) are a SEPARATE, deliberately deferred sub-feature -- see
docs/decisions.md's Phase 12 section for why: every one of those is defined
in terms of a week that has actually been played, and this league has zero
played weeks (`matchup.winner` is `UNDECIDED` everywhere), the identical
blocker Phase 9c already worked through for Weekly Recap.

This feature needs none of that. A roast is comedic PRESENTATION of facts
this codebase has already computed -- never a new analytical model, and
never an invented joke about nothing. Every sentence a roast contains cites a
specific, already-real, already-computed fact about that team, attached
alongside the joke as a `RoastFact` so the UI can show its receipts:

    - simulated title-probability rank (Playoff Planner / Beat My League's
      own `seed_odds.title_probability`, reused, never recomputed)
    - a real draft reach or a real draft steal, with the specific named
      alternative player and the real rank gap (Draft Autopsy's own
      `worst_pick` / `best_pick`), or that team's own real structural
      draft narrative when neither a real reach nor a real steal exists
    - a real zero-bench-depth positional weakness (Playoff Planner's own
      `weakest_slot` / `is_playoff_specific_weakness`, reused via
      `sim.api.beat_my_league_view`'s already-vetted-for-team-differentiation
      selection, NOT the raw `is_playoff_specific_weakness` flag directly --
      see that module's docstring for why the raw flag is not
      team-differentiating in this league)
    - a real rival threat exploiting that exact weakness, when one exists
      (`sim.api.beat_my_league_view`'s own biggest-threat selection, reused
      per team)

No new simulation path, and this module constructs no `np.random.Generator`
of its own -- everything numeric here is either a straight read of
`sim.api.beat_my_league_view`'s / `sim.api.draft_autopsy_view`'s own already-
computed output, or a deterministic rank/sort over that output.

--------------------------------------------------------------------------
Why every team's roast can be computed from ONE simulation, not eight:

`sim.api.beat_my_league_view` already splits its per-request work into
`_build_shared_materials` (the one `simulate_seasons()` call plus per-slot
sampling, run once) and `_compute_team_result` (pure per-team selection over
those already-computed materials) specifically so a caller needing this
analysis for every team in one request doesn't have to re-run the simulation
per team -- see that module's docstring point 6. This module calls
`_build_shared_materials` exactly once, then `_compute_team_result` once per
team_id, never `compute_beat_my_league()` in a loop (which would re-run the
entire pipeline once per team).

--------------------------------------------------------------------------
Draft material is optional, everything else is not:

Draft Autopsy raises `ingest.errors.DraftNotAvailableError` for a league with
no completed draft to grade (a genuinely pre-draft league, or the SYNTHETIC
validation league's pick-less mock draft -- see
`sim.api.draft_autopsy_view`'s own module docstring). Unlike every route that
depends on Draft Autopsy directly, this module does NOT let that propagate
into a 409 for the whole roast -- a roast is still a real, honest feature
without draft material (title rank, bench depth, and rival threat are all
independently real and available for the SYNTHETIC league too). `
has_draft_data=False` is surfaced on the response so the UI can say so
plainly rather than silently omitting a joke category with no explanation.

--------------------------------------------------------------------------
Editorial thresholds, the same class of choice as every other `sim.api` view
module's own presentation thresholds (`sim.api.draft_autopsy_view`'s
`_GRADE_LABEL_THRESHOLD`, `sim.api.beat_my_league_view`'s
`_STRONG_PERCENTILE_THRESHOLD`): a decision about which real, already-graded
fact is roast-worthy enough to lead with, never a fitted or invented input to
any simulation.

    `_REACH_THRESHOLD = -3.0` -- a draft pick's `value_gap` (rank-spots) has
    to be at least this far negative before it's called a "reach" in the
    roast, matching the same magnitude `sim.api.draft_autopsy_view`'s own
    `_GAP_CAUSE_THRESHOLD` uses for "a real gap, not noise."

    `_STEAL_THRESHOLD = 5.0` -- how good a team's single best pick has to be
    (positive `value_gap`, rank-spots) to earn the backhanded-compliment
    joke ("your one good pick") when that team has no real reach to roast
    instead. Set higher than `_REACH_THRESHOLD`'s magnitude because a
    backhanded compliment should require a genuinely notable pick, not just
    "slightly above average."
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg

from ingest.errors import DraftNotAvailableError
from sim.api.beat_my_league_view import (
    RivalThreat,
    TeamLeagueProfile,
    _build_shared_materials,
    _compute_team_result,
    _ordinal,
)
from sim.api.draft_autopsy_view import TeamDraftAutopsy
from sim.api.draft_autopsy_view import _compute_from_raw as _compute_draft_autopsy_from_raw
from sim.api.params_loader import load_raw_payload
from sim.api.playoff_planner_view import DEFAULT_N_SIMS

_REACH_THRESHOLD = -3.0
_STEAL_THRESHOLD = 5.0


@dataclass(frozen=True)
class RoastFact:
    """One concrete, already-computed fact backing one sentence of a roast --
    the roast's "receipts." `kind` is a stable machine-readable tag (for the
    UI to badge/group facts); `text` is a short, human-readable citation of
    the real number or named player/team the joke next to it is about."""

    kind: str
    text: str


@dataclass(frozen=True)
class TeamRoast:
    """One team's power-ranking roast: a short, good-natured paragraph, each
    sentence of which corresponds 1:1 to an entry in `facts` -- see module
    docstring. `title_rank` is this team's 1-indexed rank by simulated title
    probability (1 = best), i.e. its position in the power rankings."""

    team_id: int
    team_name: str
    title_probability: float
    title_rank: int
    league_team_count: int
    roast: str
    facts: tuple[RoastFact, ...]


@dataclass(frozen=True)
class PowerRankingRoastResult:
    league_id: int
    season_id: int
    seed: int
    n_sims: int
    # False for a league with no completed draft to grade (e.g. the
    # SYNTHETIC validation league) -- every roast is still real, just
    # without a draft-derived sentence. See module docstring.
    has_draft_data: bool
    # Ordered by title_probability descending -- the power-ranking order.
    teams: tuple[TeamRoast, ...]


def _rank_bit(team_name: str, rank: int, n_teams: int, title_probability: float) -> tuple[str, RoastFact]:
    pct = title_probability * 100
    fact = RoastFact(
        kind="title_rank",
        text=f"{_ordinal(rank)} of {n_teams} in simulated title odds, at {pct:.1f}%",
    )
    if rank == 1:
        return (
            (
                f"{team_name} tops the power rankings at {pct:.1f}% to win it all -- the simulator ran "
                "thousands of seasons and you were still the least embarrassing outcome almost every time."
            ),
            fact,
        )
    if rank == n_teams:
        return (
            (
                f"{team_name} is dead last -- {rank} of {n_teams} -- at just {pct:.1f}% to win it all. "
                "Mathematically, your season's ceiling right now is 'surprising absolutely no one.'"
            ),
            fact,
        )
    tier_size = max(1, round(n_teams / 3))
    if rank <= tier_size:
        return (
            (
                f"{team_name} sits {_ordinal(rank)} of {n_teams} at {pct:.1f}% title odds -- good enough "
                "to talk trash in the group chat, not good enough that the simulator is actually scared "
                "of you."
            ),
            fact,
        )
    if rank >= n_teams - tier_size + 1:
        return (
            (
                f"{team_name} is {_ordinal(rank)} of {n_teams} in the power rankings at {pct:.1f}% -- the "
                "simulator has run this season thousands of times and you are, on average, still bad."
            ),
            fact,
        )
    return (
        (
            f"{team_name} sits right in the middle of the pack -- {_ordinal(rank)} of {n_teams} at "
            f"{pct:.1f}% -- the simulator's official read on you is 'fine, I guess.'"
        ),
        fact,
    )


def _draft_bit(autopsy: TeamDraftAutopsy) -> tuple[str, RoastFact]:
    """Prefers a real reach (a specific, named better alternative that was
    passed over), falls back to a real steal (this team's own best pick, if
    notable enough), falls back to Draft Autopsy's own already-synthesized
    structural finding -- always a real, specific sentence, never a template,
    since Draft Autopsy guarantees `structural_finding` is always non-empty
    and grounded in that team's own numbers (see that module's docstring)."""
    worst = autopsy.worst_pick
    if worst.value_gap <= _REACH_THRESHOLD and worst.alternative_player_name is not None:
        gap = abs(worst.value_gap)
        fact = RoastFact(
            kind="draft_reach",
            text=(
                f"Pick {worst.overall_pick_number}: took {worst.player_name} (rank {worst.player_rank}) "
                f"over {worst.alternative_player_name} (rank {worst.alternative_player_rank})"
            ),
        )
        return (
            (
                f"At pick {worst.overall_pick_number}, you took {worst.player_name} over "
                f"{worst.alternative_player_name}, who was sitting right there ranked {gap:.0f} spots "
                "better at the same position. Bold read on the board."
            ),
            fact,
        )

    best = autopsy.best_pick
    if best.value_gap >= _STEAL_THRESHOLD and best.alternative_player_name is not None:
        fact = RoastFact(
            kind="draft_best_pick",
            text=(
                f"Pick {best.overall_pick_number}: {best.player_name} graded {best.value_gap:.0f} "
                "rank-spots ahead of the best same-position alternative on the board"
            ),
        )
        return (
            (
                f"Your one genuinely good pick was {best.player_name} at pick "
                f"{best.overall_pick_number} -- {best.value_gap:.0f} rank-spots of pure theft. "
                "Everything else in that draft was... fine."
            ),
            fact,
        )

    fact = RoastFact(kind="draft_structural", text=autopsy.structural_finding)
    return (autopsy.structural_finding, fact)


def _weakness_bit(profile: TeamLeagueProfile) -> tuple[str, RoastFact] | None:
    if not profile.weaknesses:
        return None
    slot = profile.weaknesses[0]
    depth_note = "zero" if not slot.has_bench_depth else "some"
    fact = RoastFact(
        kind="bench_depth",
        text=(
            f"{slot.slot_label}: ranked {slot.league_rank} of {slot.league_team_count} in the league, "
            f"{depth_note} same-position bench depth behind it"
        ),
    )
    if slot.is_playoff_specific_weakness:
        return (
            (
                f"Your {slot.slot_label} spot has zero bench depth behind it -- ranked "
                f"{slot.league_rank} of {slot.league_team_count} teams in the league there. One bad "
                "injury report away from a bye week you didn't schedule."
            ),
            fact,
        )
    return (
        (
            f"If we're really nitpicking, {slot.slot_label} is your least-strong slot, ranked "
            f"{slot.league_rank} of {slot.league_team_count} -- which, relatively speaking, is basically "
            "a compliment."
        ),
        fact,
    )


def _rival_threat_bit(threat: RivalThreat) -> tuple[str, RoastFact] | None:
    if not threat.overlapping_slots:
        return None
    slot_label = threat.overlapping_slots[0]
    pct = threat.title_probability * 100
    fact = RoastFact(
        kind="rival_threat",
        text=f"{threat.team_name} is strong at {slot_label} ({pct:.1f}% title odds) -- your own weak spot",
    )
    return (
        (
            f"Keep an eye on {threat.team_name}: real strength at {slot_label} -- exactly your soft "
            f"spot -- and they're sitting at {pct:.1f}% to win it all. They know."
        ),
        fact,
    )


def _synthesize_roast(
    rank: int,
    n_teams: int,
    profile: TeamLeagueProfile,
    threat: RivalThreat,
    autopsy: TeamDraftAutopsy | None,
) -> tuple[str, tuple[RoastFact, ...]]:
    """Assembles a team's roast from whichever real bits are actually
    available for them -- a genuinely different fact set per team (see
    module docstring), never a fill-in-the-blank template. Order: rank
    (always present) -> draft (when Draft Autopsy has data for this league)
    -> bench depth -> rival threat (both real, honest 'nothing here'
    possibilities that are silently omitted rather than forced)."""
    sentences: list[str] = []
    facts: list[RoastFact] = []

    rank_sentence, rank_fact = _rank_bit(profile.team_name, rank, n_teams, profile.title_probability)
    sentences.append(rank_sentence)
    facts.append(rank_fact)

    if autopsy is not None:
        draft_sentence, draft_fact = _draft_bit(autopsy)
        sentences.append(draft_sentence)
        facts.append(draft_fact)

    weakness = _weakness_bit(profile)
    if weakness is not None:
        sentences.append(weakness[0])
        facts.append(weakness[1])

    rival = _rival_threat_bit(threat)
    if rival is not None:
        sentences.append(rival[0])
        facts.append(rival[1])

    return " ".join(sentences), tuple(facts)


def compute_power_ranking_roast(
    conn: psycopg.Connection[Any],
    league_id: int,
    season_id: int,
    *,
    n_sims: int = DEFAULT_N_SIMS,
) -> PowerRankingRoastResult:
    """Build the power ranking roast for every team in one league/season.
    Raises `LeagueNotIngestedError` (via `load_raw_payload`) if the
    league/season was never ingested, and the same
    `ingest.errors.RosterNotAvailableError` / `MissingProjectionError`
    `sim.api.playoff_planner_view` already raises for a league with no
    drafted, fully-projectable roster -- both propagate unchanged, same
    convention as every other `sim.api` view module."""
    raw = load_raw_payload(conn, league_id, season_id)
    return _compute_from_raw(raw, league_id, season_id, n_sims=n_sims)


def _compute_from_raw(
    raw: Mapping[str, Any], league_id: int, season_id: int, *, n_sims: int
) -> PowerRankingRoastResult:
    """The actual computation, factored out so it can be exercised directly
    against an in-memory fixture dict (no Postgres needed) -- same
    fast-unit-tests-plus-thin-integration-test split every other `sim.api`
    view module already established."""
    materials = _build_shared_materials(raw, league_id, season_id, n_sims=n_sims)

    autopsy_by_team: dict[int, TeamDraftAutopsy] = {}
    has_draft_data = True
    try:
        autopsy = _compute_draft_autopsy_from_raw(raw, league_id, season_id)
        autopsy_by_team = {t.team_id: t for t in autopsy.teams}
    except DraftNotAvailableError:
        # A real, honest possibility (the SYNTHETIC validation league, or a
        # genuinely pre-draft league) -- see module docstring for why this
        # does NOT propagate into a 409 for the whole roast the way it does
        # for GET /league/{id}/draft-autopsy directly.
        has_draft_data = False

    # Power-ranking order: title_probability descending, team_id as a
    # deterministic tiebreak for the (practically impossible) case of an
    # exact tie.
    ranked_profiles = sorted(materials.profiles, key=lambda p: (-p.title_probability, p.team_id))
    n_teams = len(ranked_profiles)

    teams_out: list[TeamRoast] = []
    for rank, profile in enumerate(ranked_profiles, start=1):
        threat, _advantage, _cautions = _compute_team_result(profile.team_id, materials)
        team_autopsy = autopsy_by_team.get(profile.team_id)
        roast_text, facts = _synthesize_roast(rank, n_teams, profile, threat, team_autopsy)
        teams_out.append(
            TeamRoast(
                team_id=profile.team_id,
                team_name=profile.team_name,
                title_probability=profile.title_probability,
                title_rank=rank,
                league_team_count=n_teams,
                roast=roast_text,
                facts=facts,
            )
        )

    return PowerRankingRoastResult(
        league_id=league_id,
        season_id=season_id,
        seed=materials.planner.seed,
        n_sims=materials.planner.n_sims,
        has_draft_data=has_draft_data,
        teams=tuple(teams_out),
    )
