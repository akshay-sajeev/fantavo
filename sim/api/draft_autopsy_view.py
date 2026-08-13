"""Read-only draft-pick grading, backing `GET /league/{id}/draft-autopsy`.

Covers PLAN.md's Phase 7 (feature 5): per-pick value vs. what was actually
available, best/worst decisions with a specific named alternative, positional
strategy grades, and a structural finding synthesized from the pick-level
data. Explicitly NOT draft replay (PLAN.md's deferred section) -- nothing
here models how any other manager would have drafted differently. Every
number is a deterministic function of the *actual* pick sequence
(`draftDetail.picks`) and the fixture's own rank data; the only
counterfactual ever asked is "who else, from the real remaining player pool,
was still on the board when this specific pick happened" -- not "what would
have happened next."

Only ever usable against a league whose fixture has a real completed draft.
The SYNTHETIC validation league (`scripts/ingest_synthetic_league.py`) has
`draftDetail.picks == []` by construction -- its mock draft fabricates
rosters directly, with no pick sequence to grade -- so `compute_draft_autopsy`
raises `ingest.errors.DraftNotAvailableError` for it, same as for a
genuinely pre-draft league. This feature was built and is only demonstrable
against the real ingested league (league_id=885686492).

--------------------------------------------------------------------------
Rank source and data-provenance decision (see docs/decisions.md Phase 7 for
the full writeup):

    `draftRanksByRankType["PPR"]["rank"]` -- ESPN's own overall consensus
    rank under PPR scoring -- is used as the single ranking signal for "how
    good is this player," because this league scores full PPR (see
    docs/decisions.md Phase 1). It is a clean total order: every one of the
    428 players this fixture carries stats for (all 300 free agents plus all
    128 rostered players, see `ingest.parse.every_player_with_stats`) has a
    distinct, positive PPR rank -- verified directly against
    fixtures/league_raw_2026.json, not assumed.

    `ownership.averageDraftPosition` is carried alongside as `player_adp` on
    every graded pick -- a secondary, display-only cross-check denominated in
    the same units as `overallPickNumber` -- but is never used to decide a
    pick's grade, best/worst status, or the alternative shown.

    Both fields are stamped with ESPN's live consensus data as of when the
    fixture was fetched (`ownership.date`), which is a few days *after*
    `draftDetail.completeDate` (this league's draft completed 2026-08-05;
    the fixture's ownership timestamps read ~2026-08-13). This is a
    deliberate, documented proxy, not an oversight: PLAN.md's instruction is
    "grade picks against players available at that pick, not against final
    season outcomes" -- and the 2026 season has not started anywhere in this
    league's data (every `matchup.winner` is still "UNDECIDED" league-wide,
    confirmed elsewhere in docs/decisions.md), so this rank data cannot be
    contaminated by real game results by construction. The few-day gap can
    only reflect ordinary preseason noise (roster/depth-chart news, injury
    updates) that shifted ESPN's consensus rank slightly after draft day --
    the same kind of drift a live draft-day ADP feed experiences constantly,
    not a hindsight leak from games actually played. Concluded this is an
    acceptable, honestly-documented proxy for "value available at the pick,"
    consistent with PLAN.md's own instruction rather than a violation of it.
--------------------------------------------------------------------------

Two distinct "position" concepts are used throughout and must not be
conflated:

- `position` on a DraftPickGrade is the drafted player's own
  `defaultPositionId`-derived label (QB/RB/WR/TE/K/D-ST) -- used to find a
  same-position alternative regardless of which roster slot ESPN
  auto-assigned the pick to.
- `grade_bucket` is the summary category PLAN.md explicitly asks for
  (QB/RB/WR/TE/Bench) and is driven by the pick's own `lineupSlotId` at
  draft time (0/2/4/6/20, with a FLEX pick (23) resolved to the player's own
  position): a pick auto-slotted to the bench at draft time is graded in the
  "Bench" bucket regardless of the player's position, and K/D-ST picks are
  excluded from the bucket summary entirely, matching PLAN.md's literal
  five-category list. `lineupSlotId` at draft time is used rather than the
  player's *current* roster slot (some players have since been traded or
  waived -- see docs/decisions.md) because grading should reflect the pick
  that was actually made, not later roster churn.

Deliberately not vectorized with NumPy: this is discrete bookkeeping over a
fixed 128-pick sequence (128 picks x up to 428 candidate alternatives, ~55k
comparisons worst case), not a Monte Carlo simulation -- CLAUDE.md's
"vectorize" rule targets `sim.engine`'s per-simulation arrays, not this kind
of one-off combinatorial pass over already-fixed historical data.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from statistics import mean
from typing import Any

import psycopg

from ingest.errors import DraftNotAvailableError, MissingRankDataError
from ingest.parse import every_player_with_stats, parse_teams
from ingest.slots import BENCH_SLOT_ID, DEFAULT_POSITION_NAMES, SLOT_NAMES
from sim.api.params_loader import load_raw_payload

FLEX_SLOT_ID = 23

# The five categories PLAN.md's positional-strategy-grade requirement names
# explicitly. K and D/ST picks are real, graded picks (they appear in the
# full per-pick board with their own value_gap) but are not rolled into this
# specific summary -- matching PLAN.md's literal list rather than inventing
# a sixth/seventh category it didn't ask for.
_GRADED_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE")
_BENCH_BUCKET = "Bench"

_RANK_SOURCE_LABEL = "ESPN draftRanksByRankType.PPR.rank (full-PPR consensus overall rank)"

# Editorial thresholds for turning an already-computed number into a
# human-readable label/narrative -- the same class of choice as
# TeamRiskCard's riskBand() cutoffs in the web layer (0.05 / 0.15) or this
# module's own sibling `roster_view.py`'s 10th/90th percentile choice: a
# presentation decision over a real, already-derived number, not a fitted or
# invented input to any simulation. Documented here and in docs/decisions.md
# rather than left as bare magic numbers.
_GRADE_LABEL_THRESHOLD = 3.0  # rank-spots vs. the league average for a bucket
_LATE_TIMING_THRESHOLD = 6.0  # picks later than the league's average first pick at a position
_GAP_CAUSE_THRESHOLD = 3.0  # rank-spots worse than the league average at a position


@dataclass(frozen=True)
class DraftPickGrade:
    """One real draft pick, graded against the specific alternative that was
    still on the board (same position, excluding the player actually taken)
    at the moment of the pick.

    `value_gap = alternative_player_rank - player_rank` when an alternative
    existed, else 0.0:
      - positive: the player taken was ranked better than the best remaining
        same-position alternative -- a correct-process pick.
      - negative: a better-ranked player at the same position was still on
        the board and passed over -- a reach.
      - zero with `alternative_player_id is None`: no other player at this
        position remained in the fixture's tracked pool (300 free agents +
        128 rostered) at this point in the draft -- not graded either way,
        since there was genuinely nothing left to compare against.

    `best_overall_available_*` is informational only (the single best-ranked
    player left on the board, any position) -- shown for context, never used
    to decide a grade, since it would unfairly penalize a mandatory QB/K/D-ST
    fill against whatever RB/WR happened to be sitting there (see module
    docstring).
    """

    overall_pick_number: int
    round_id: int
    round_pick_number: int
    team_id: int
    team_name: str
    player_id: int
    player_name: str
    position: str
    slot_label: str
    grade_bucket: str | None
    player_rank: int
    player_adp: float | None
    alternative_player_id: int | None
    alternative_player_name: str | None
    alternative_player_rank: int | None
    value_gap: float
    best_overall_available_player_id: int | None
    best_overall_available_player_name: str | None
    best_overall_available_rank: int | None


@dataclass(frozen=True)
class PositionGrade:
    """One team's summary at one of the five PLAN.md-named buckets
    (QB/RB/WR/TE/Bench): its own average value_gap against the league-wide
    average value_gap for the same bucket, plus a presentation label."""

    position: str
    pick_count: int
    avg_value_gap: float
    league_avg_value_gap: float
    label: str


@dataclass(frozen=True)
class PositionTiming:
    """Per-position (QB/RB/WR/TE, by the player's own position label --
    starters and bench picks alike, unlike PositionGrade's bucket split)
    timing and value facts, used both as supporting evidence for the
    structural finding and as the raw material `_synthesize_structural_finding`
    reasons over."""

    position: str
    team_first_pick_number: int
    team_first_pick_round: int
    league_avg_first_pick_number: float
    team_pick_count: int
    team_avg_value_gap: float
    league_avg_value_gap: float


@dataclass(frozen=True)
class TeamDraftAutopsy:
    team_id: int
    team_name: str
    picks: tuple[DraftPickGrade, ...]
    best_pick: DraftPickGrade
    worst_pick: DraftPickGrade
    position_grades: tuple[PositionGrade, ...]
    position_timing: tuple[PositionTiming, ...]
    structural_finding: str


@dataclass(frozen=True)
class DraftAutopsy:
    league_id: int
    season_id: int
    rank_source: str
    teams: tuple[TeamDraftAutopsy, ...]


def _rank_and_adp(player_id: int, player: Mapping[str, Any]) -> tuple[int, float | None]:
    drbr = player.get("draftRanksByRankType") or {}
    ppr = drbr.get("PPR") or {}
    rank = ppr.get("rank")
    if not isinstance(rank, int) or rank <= 0:
        raise MissingRankDataError(
            f"player {player_id} ({player.get('fullName', '?')}) has no usable "
            f"draftRanksByRankType.PPR.rank -- cannot grade draft picks around this "
            "player without a rank signal"
        )
    ownership = player.get("ownership") or {}
    adp = ownership.get("averageDraftPosition")
    adp_f = float(adp) if isinstance(adp, int | float) else None
    return rank, adp_f


def _build_position_pools(
    players: Mapping[int, Any], rank_by_id: Mapping[int, int]
) -> dict[str, list[int]]:
    """player_ids grouped by their own defaultPositionId label, each list
    sorted ascending by rank (best first) so the first not-yet-drafted entry
    in a scan is always the best remaining alternative at that position."""
    pool: dict[str, list[int]] = defaultdict(list)
    for player_id, player in players.items():
        position = DEFAULT_POSITION_NAMES.get(player.get("defaultPositionId"), "?")
        pool[position].append(player_id)
    for ids in pool.values():
        ids.sort(key=lambda pid: rank_by_id[pid])
    return pool


def _best_available(pool_ids: list[int], drafted: set[int], exclude_id: int) -> int | None:
    """First not-yet-drafted, non-self id in a rank-sorted pool -- the best
    remaining alternative. None if nothing is left to compare against."""
    for pid in pool_ids:
        if pid == exclude_id or pid in drafted:
            continue
        return pid
    return None


def _grade_bucket(slot_id: int, position: str) -> str | None:
    if slot_id == BENCH_SLOT_ID:
        return _BENCH_BUCKET
    if slot_id == 0:
        return "QB"
    if slot_id == 2:
        return "RB"
    if slot_id == 4:
        return "WR"
    if slot_id == 6:
        return "TE"
    if slot_id == FLEX_SLOT_ID:
        return position if position in _GRADED_POSITIONS else None
    return None  # K, D/ST, or any other slot -- not one of PLAN.md's 5 buckets


def _grade_label(avg_gap: float, league_avg: float) -> str:
    delta = avg_gap - league_avg
    if delta >= _GRADE_LABEL_THRESHOLD:
        return "Above the field"
    if delta <= -_GRADE_LABEL_THRESHOLD:
        return "Below the field"
    return "On par with the field"


def _synthesize_structural_finding(
    team_name: str, timing: tuple[PositionTiming, ...]
) -> str:
    """A real narrative built from this team's own position-timing facts
    compared against the league average at each position -- never a template
    filled with just the team name. See module docstring for the threshold
    rationale.
    """
    if not timing:
        return (
            f"{team_name} has no graded QB/RB/WR/TE picks to analyze for this draft."
        )

    def delta_timing(t: PositionTiming) -> float:
        return t.team_first_pick_number - t.league_avg_first_pick_number

    def delta_gap(t: PositionTiming) -> float:
        # positive means this team's picks at the position graded WORSE
        # (lower value_gap) than the league average at that position.
        return t.league_avg_value_gap - t.team_avg_value_gap

    causal_candidates = [
        t for t in timing if delta_timing(t) > _LATE_TIMING_THRESHOLD and delta_gap(t) > _GAP_CAUSE_THRESHOLD
    ]

    sentences: list[str] = []

    if causal_candidates:
        primary = max(causal_candidates, key=delta_gap)
        sentences.append(
            f"You didn't take your first {primary.position} until pick "
            f"{primary.team_first_pick_number} (round {primary.team_first_pick_round}) -- "
            f"about {delta_timing(primary):.0f} picks after the league's average first "
            f"{primary.position} pick (around pick {primary.league_avg_first_pick_number:.0f}). "
            f"By the time you circled back, your {primary.position} picks graded "
            f"{delta_gap(primary):.1f} rank-spots below the best available option on "
            f"average, versus the league's own average gap of "
            f"{primary.league_avg_value_gap:.1f} at that position -- the position had "
            "thinned out while you waited."
        )
        remaining = [t for t in timing if t.position != primary.position]
    else:
        worst = min(timing, key=lambda t: -delta_gap(t))
        if delta_gap(worst) > _GAP_CAUSE_THRESHOLD:
            sentences.append(
                f"Your weakest position was {worst.position}: those picks graded "
                f"{delta_gap(worst):.1f} rank-spots below the board's best available "
                f"option on average, versus the league's average gap of "
                f"{worst.league_avg_value_gap:.1f} at that position -- not clearly "
                f"explained by timing, since your first {worst.position} pick "
                f"(pick {worst.team_first_pick_number}, round {worst.team_first_pick_round}) "
                f"was close to the league's average (around pick "
                f"{worst.league_avg_first_pick_number:.0f})."
            )
        else:
            sentences.append(
                "No single position stands out here: your pick timing and the value "
                "you got at QB, RB, WR and TE all landed close to the league average "
                "across this draft."
            )
        remaining = [t for t in timing if t is not worst]

    if remaining:
        best_value = max(remaining, key=lambda t: -delta_gap(t))
        if -delta_gap(best_value) > _GAP_CAUSE_THRESHOLD:
            sentences.append(
                f"Your best value came at {best_value.position}, beating the board by "
                f"{-delta_gap(best_value):.1f} rank-spots on average against a league "
                f"average gap of {best_value.league_avg_value_gap:.1f} there."
            )

    return " ".join(sentences)


def compute_draft_autopsy(
    conn: psycopg.Connection[Any], league_id: int, season_id: int
) -> DraftAutopsy:
    """Grade every real pick in this league's draft against the specific
    same-position alternative that was actually still on the board, roll
    those grades into positional summaries, and synthesize a structural
    finding per team. Raises `ingest.errors.DraftNotAvailableError` (via a
    direct check on `draftDetail`) if there is no real pick sequence to
    grade, and `ingest.errors.MissingRankDataError` if any player this
    computation needs a rank for lacks one -- both propagate unchanged to
    the route handler, same convention as every other `sim.api` view module.
    """
    raw = load_raw_payload(conn, league_id, season_id)
    return _compute_from_raw(raw, league_id, season_id)


def _compute_from_raw(raw: Mapping[str, Any], league_id: int, season_id: int) -> DraftAutopsy:
    """The actual grading pipeline, factored out from `compute_draft_autopsy`
    so it can be exercised directly against an in-memory fixture dict (no
    Postgres connection needed) -- used by sim/tests/test_api_draft_autopsy.py
    for fast, DB-independent coverage of the grading logic itself, with a
    thin separate test for the Postgres-backed route/`load_raw_payload` path.
    """
    draft_detail = raw.get("draftDetail") or {}
    picks_raw = draft_detail.get("picks") or []
    if not draft_detail.get("drafted") or not picks_raw:
        raise DraftNotAvailableError(
            f"league_id={league_id} season_id={season_id} has no completed draft to "
            "autopsy (draftDetail.drafted is falsy, or draftDetail.picks is empty) -- "
            "this is expected for a genuinely pre-draft league and for the SYNTHETIC "
            "validation league, whose mock draft fabricates rosters directly without "
            "a real pick sequence (see scripts/ingest_synthetic_league.py)"
        )

    players = every_player_with_stats(raw)
    rank_by_id: dict[int, int] = {}
    adp_by_id: dict[int, float | None] = {}
    for player_id, player in players.items():
        rank, adp = _rank_and_adp(player_id, player)
        rank_by_id[player_id] = rank
        adp_by_id[player_id] = adp

    position_pools = _build_position_pools(players, rank_by_id)
    overall_pool = sorted(rank_by_id, key=lambda pid: rank_by_id[pid])

    teams = parse_teams(raw)
    team_name_by_id = {t.team_id: t.name for t in teams}

    picks_sorted = sorted(picks_raw, key=lambda p: p["overallPickNumber"])

    drafted: set[int] = set()
    grades: list[DraftPickGrade] = []
    for pick in picks_sorted:
        player_id = pick["playerId"]
        player = players.get(player_id)
        if player is None:
            raise MissingRankDataError(
                f"draft pick {pick['overallPickNumber']} references player_id="
                f"{player_id}, which has no entry in this fixture's player pool "
                "(ingest.parse.every_player_with_stats) -- cannot grade this pick "
                "without a rank for the player actually taken"
            )
        position = DEFAULT_POSITION_NAMES.get(player.get("defaultPositionId"), "?")
        rank = rank_by_id[player_id]
        adp = adp_by_id[player_id]

        alt_id = _best_available(position_pools.get(position, []), drafted, player_id)
        alt_rank = rank_by_id[alt_id] if alt_id is not None else None
        alt_name = players[alt_id]["fullName"] if alt_id is not None else None
        value_gap = float(alt_rank - rank) if alt_rank is not None else 0.0

        best_id = _best_available(overall_pool, drafted, player_id)
        best_rank = rank_by_id[best_id] if best_id is not None else None
        best_name = players[best_id]["fullName"] if best_id is not None else None

        slot_id = pick["lineupSlotId"]
        grades.append(
            DraftPickGrade(
                overall_pick_number=pick["overallPickNumber"],
                round_id=pick["roundId"],
                round_pick_number=pick["roundPickNumber"],
                team_id=pick["teamId"],
                team_name=team_name_by_id.get(pick["teamId"], f"Team {pick['teamId']}"),
                player_id=player_id,
                player_name=player["fullName"],
                position=position,
                slot_label=SLOT_NAMES.get(slot_id, str(slot_id)),
                grade_bucket=_grade_bucket(slot_id, position),
                player_rank=rank,
                player_adp=adp,
                alternative_player_id=alt_id,
                alternative_player_name=alt_name,
                alternative_player_rank=alt_rank,
                value_gap=value_gap,
                best_overall_available_player_id=best_id,
                best_overall_available_player_name=best_name,
                best_overall_available_rank=best_rank,
            )
        )
        drafted.add(player_id)

    # League-wide average value_gap per PLAN.md-named bucket, for each
    # team's PositionGrade.label to compare itself against.
    league_bucket_gaps: dict[str, list[float]] = defaultdict(list)
    for g in grades:
        if g.grade_bucket is not None:
            league_bucket_gaps[g.grade_bucket].append(g.value_gap)
    league_avg_gap_by_bucket = {b: mean(v) for b, v in league_bucket_gaps.items()}

    # League-wide first-pick-number and average value_gap per *position label*
    # (not bucket -- bench picks count here), for the structural-finding synthesis.
    first_pick_by_team_position: dict[tuple[int, str], int] = {}
    round_by_team_position: dict[tuple[int, str], int] = {}
    league_position_gaps: dict[str, list[float]] = defaultdict(list)
    for g in grades:
        league_position_gaps[g.position].append(g.value_gap)
        key = (g.team_id, g.position)
        if key not in first_pick_by_team_position or g.overall_pick_number < first_pick_by_team_position[key]:
            first_pick_by_team_position[key] = g.overall_pick_number
            round_by_team_position[key] = g.round_id
    league_avg_gap_by_position = {p: mean(v) for p, v in league_position_gaps.items()}
    league_avg_first_pick_by_position: dict[str, float] = {}
    for position in _GRADED_POSITIONS:
        firsts = [pick for (_tid, pos), pick in first_pick_by_team_position.items() if pos == position]
        if firsts:
            league_avg_first_pick_by_position[position] = mean(firsts)

    by_team: dict[int, list[DraftPickGrade]] = defaultdict(list)
    for g in grades:
        by_team[g.team_id].append(g)

    teams_out: list[TeamDraftAutopsy] = []
    for team in teams:
        team_picks = tuple(
            sorted(by_team.get(team.team_id, []), key=lambda g: g.overall_pick_number)
        )
        if not team_picks:
            continue  # defensive: a completed draft should give every team its picks

        best_pick = max(team_picks, key=lambda g: (g.value_gap, -g.overall_pick_number))
        worst_pick = min(team_picks, key=lambda g: (g.value_gap, g.overall_pick_number))

        position_grades: list[PositionGrade] = []
        for bucket in (*_GRADED_POSITIONS, _BENCH_BUCKET):
            bucket_picks = [g for g in team_picks if g.grade_bucket == bucket]
            if not bucket_picks:
                continue
            avg_gap = mean(g.value_gap for g in bucket_picks)
            league_avg = league_avg_gap_by_bucket.get(bucket, avg_gap)
            position_grades.append(
                PositionGrade(
                    position=bucket,
                    pick_count=len(bucket_picks),
                    avg_value_gap=avg_gap,
                    league_avg_value_gap=league_avg,
                    label=_grade_label(avg_gap, league_avg),
                )
            )

        position_timing: list[PositionTiming] = []
        for position in _GRADED_POSITIONS:
            key = (team.team_id, position)
            first_pick = first_pick_by_team_position.get(key)
            position_picks = [g for g in team_picks if g.position == position]
            if first_pick is None or not position_picks:
                continue
            team_avg_gap = mean(g.value_gap for g in position_picks)
            position_timing.append(
                PositionTiming(
                    position=position,
                    team_first_pick_number=first_pick,
                    team_first_pick_round=round_by_team_position[key],
                    league_avg_first_pick_number=league_avg_first_pick_by_position.get(
                        position, float(first_pick)
                    ),
                    team_pick_count=len(position_picks),
                    team_avg_value_gap=team_avg_gap,
                    league_avg_value_gap=league_avg_gap_by_position.get(position, team_avg_gap),
                )
            )

        teams_out.append(
            TeamDraftAutopsy(
                team_id=team.team_id,
                team_name=team.name,
                picks=team_picks,
                best_pick=best_pick,
                worst_pick=worst_pick,
                position_grades=tuple(position_grades),
                position_timing=tuple(position_timing),
                structural_finding=_synthesize_structural_finding(team.name, tuple(position_timing)),
            )
        )

    return DraftAutopsy(
        league_id=league_id,
        season_id=season_id,
        rank_source=_RANK_SOURCE_LABEL,
        teams=tuple(teams_out),
    )
