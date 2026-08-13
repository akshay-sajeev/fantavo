"""Waiver Intelligence, backing `GET /league/{id}/waiver-intelligence/{team_id}`.

Covers PLAN.md's Phase 9 "Weekly loop" sub-feature "Waiver intelligence"
(feature 9): score every real free agent on four distinct signals --
opportunity, availability, league fit, competition -- and return a ranked
priority list with a real, per-team reasoning sentence attached to each
name. Explicitly the SECOND of Phase 9's three sub-features (Lineup
optimizer is Phase 9a; Weekly recap is a separate future session).

No `simulate_seasons()` anywhere in this module, and no RNG at all --
unlike every other `sim.api` view module, there is nothing stochastic here
to seed. See "Why this doesn't call simulate_seasons()" near the end of
this docstring for the full reasoning (a real, considered extension was on
the table and set aside for this phase specifically, not rejected as
illegitimate).

--------------------------------------------------------------------------
The `_freeAgents` block, and why no widening is needed here (unlike every
other Phase 9-and-earlier feature):

Every prior phase that touched the player pool needed
`ingest.parse.every_player_with_stats` -- the union of `_freeAgents` and
every team's drafted roster -- because those features needed projections
for players who are NOW rostered. Waiver intelligence is the opposite case:
PLAN.md's "use the `_freeAgents` block" instruction is read literally,
because `_freeAgents` means exactly the right thing for THIS feature --
"who is actually available to add in this league, right now" -- now that
the league has drafted (see docs/decisions.md, "No more mock data": ESPN
removes a player from `_freeAgents` the moment they're drafted). No union,
no widening: `raw["_freeAgents"]` is used directly to build the candidate
pool.

--------------------------------------------------------------------------
Data-provenance note (see docs/decisions.md Phase 9b for the full writeup,
matching the honesty precedent Phase 7 set for `draftRanksByRankType`):

`ownership.percentOwned`, `percentStarted`, `percentChange`,
`averageDraftPosition`, and `player.injuryStatus` are ESPN's own
CROSS-LEAGUE consensus figures -- aggregated across many ESPN leagues, not
computed from this specific league's own transaction history (this league
has no waiver-claim or add/drop history ingested anywhere; ESPN's fixture
format doesn't expose one). This is real, honest, non-invented ESPN data,
but it answers "how does the broader ESPN player pool feel about this
player" rather than "who in THIS specific league's manager pool would want
him," which League fit / Competition (Signals 3/4) answer instead, computed
entirely from this league's own real rosters. Two fields investigated and
found NOT usable in this fixture, so not used anywhere in this module:
`ownership.activityLevel` is `null` for all 300 free agents (verified
directly, not assumed), and `player.active` is `True` for all 300 free
agents and all 128 rostered players alike -- a constant carries zero
information, so using either would be indistinguishable from inventing a
fixed value.

--------------------------------------------------------------------------
The four signals, and exactly how each is computed from real data:

1. OPPORTUNITY (realistic playing time, role/usage uncertainty --
   deliberately NOT "is this a good player," which is just
   `mean_points_per_game`):

       opportunity_score = 0.0                                    if
                            player.injuryStatus in {OUT, INJURY_RESERVE}
                          = clip(percent_started / percent_owned, 0, 1)
                            otherwise

   `percent_started / percent_owned` is a real, directly-computed ratio:
   among the ESPN teams that own this player, what fraction actually start
   him. Verified live against the fixture that this is a genuine,
   informative signal: RB/WR free agents with many roster spots per team
   cluster far lower even at HIGH ownership (e.g. Patrick Mahomes: 85%
   owned, only 18% started -- QUESTIONABLE at fetch time; Alvin Kamara: 50%
   owned, only 2% started, ADP 156.7 -- a late-round handcuff/depth stash,
   not a trusted starter). That's exactly "role/usage uncertainty": whether
   other managers who already own this exact player trust him enough to
   start him, independent of how talented he is.

   The `injuryStatus in {OUT, INJURY_RESERVE}` override forces the score to
   the natural floor of the ratio's own [0, 1] range rather than blending in
   an invented penalty coefficient -- OUT/IR is a factual "not playing right
   now" statement, not an editorial weighting choice. QUESTIONABLE does NOT
   override: the ratio itself already reflects real managers discounting a
   questionable player's start rate, so adding a second, invented penalty
   would double-count real information with a made-up number.
   `injury_status` is still surfaced as its own field and used in the
   reasoning text.

   `averageDraftPosition` relative to this player's own projection rank WAS
   investigated as a candidate opportunity signal (PLAN.md explicitly
   suggests it) and deliberately NOT folded into `opportunity_score`: a gap
   between draft-day consensus and this player's own current point
   projection measures market-vs-model VALUE disagreement, not role/usage
   certainty -- exactly the "already a good player" conflation PLAN.md's own
   instruction warns against. `average_draft_position` is still surfaced as
   a separate, honest, display-only field (same "context, never blended"
   treatment Phase 7 gave `player_adp`), not discarded.

2. AVAILABILITY (how widely rostered) -- `percent_owned` (ESPN's
   `ownership.percentOwned`), used exactly as PLAN.md names it, with no
   inversion or transformation. Presented as context, not a directional
   "good/bad" score: a HIGH percent_owned free agent still on THIS league's
   wire is the more surprising, often more valuable fact (a widely-valued
   player this league's managers haven't grabbed yet) -- the opposite of
   "low ownership = hidden gem" intuition. It deliberately does not drive
   the ranking numerically (see "Ranking" below); it drives the reasoning
   text's framing instead.

3 & 4. LEAGUE FIT (does the REQUESTING team need this position) and
   COMPETITION (which rival teams also need it) -- both reuse
   `sim.api.roster_view`'s `positional_concentration` /
   `sim.api.playoff_planner_view`'s `bench_position_counts` RULE exactly
   (count a team's own bench/IR entries by their real `defaultPositionId`
   label), reimplemented locally for the same reason `playoff_planner_view`'s
   own docstring gives for doing the identical thing: the rule is shared,
   the local data shape is not. League fit and Competition are computed
   ONCE PER POSITION, not once per candidate -- see "Why this is grouped by
   position, not one flat list" below for why a position, not a player, is
   the right grain for these two signals.

   CAUGHT BEFORE SHIPPING, the same "verify this is actually team-
   differentiating before using it" discipline `sim.api.playoff_planner_view`
   already applied to its own bench-depth signal (see that module's
   docstring and docs/decisions.md Phase 8): `team_bench_depth_at_position`
   at K is 0 for literally every one of this league's 8 real teams (verified
   directly against the fixture -- zero teams carry a backup kicker at all),
   and at D/ST only 2 of 8 carry any bench depth. K and D/ST are single-slot
   positions ESPN-style leagues structurally almost never bench -- "zero
   bench depth" there is a near-constant fact about how fantasy rosters are
   built, not a real, per-team differentiating "need." League fit /
   Competition are therefore only ever computed from real bench-depth data
   for QB/RB/WR/TE (the same four positions `sim.api.draft_autopsy_view`
   already treats as the ones with a real bench-depth strategy, see
   `_GRADED_POSITIONS` there); K and D/ST get an honest "not a real roster
   decision here" sentence instead of a fabricated need/no-need claim.

--------------------------------------------------------------------------
Why this is grouped by position, not one flat list -- ALSO caught before
shipping, the second real design problem this investigation surfaced:

A first draft of this module sorted every candidate, across every position,
into one flat list by `expected_playable_points` (`mean_points_per_game *
opportunity_score`). Run live against the real league, every team's list was
dominated top-to-bottom by kickers. The cause is structural, not a bug in
any one number: `opportunity_score` (Signal 1) is a WITHIN-position "trust"
ratio, and K/D-ST structurally have almost no bench competition at their own
position (see the "caught before shipping" note above) -- there is rarely a
second rostered kicker to lose a start to -- so kickers cluster near a 0.9+
start-rate ratio while RB/WR free agents, who by definition are mostly
backups/committee pieces sitting on someone else's bench, structurally
cluster much lower. Multiplying that ratio by a comparable per-game mean
(a solid kicker and a low-end skill-position free agent often project
similarly, 7-9 pts/game) means `expected_playable_points` is not an
apples-to-apples number ACROSS positions -- it never was, and using it in one
flat cross-position sort was the actual bug, not the formula itself.

Fix: candidates are grouped by position, and `expected_playable_points`
only ever ranks candidates WITHIN a group, where the comparison is honest.
Groups are then ordered: positions where THIS team has a real positional
need (Signal 3, QB/RB/WR/TE only) lead, in canonical position order
(`_POSITION_DISPLAY_ORDER`); positions with real bench-depth data but no
current need follow; K and D/ST -- where bench depth isn't a real
decision -- come last, explicitly framed as streaming options rather than
roster needs. This also lets League fit / Competition (Signals 3/4, which
are genuinely position-level, not player-level, facts) be stated ONCE per
group instead of being repeated verbatim on every candidate row, which the
first flat-list draft also did.

--------------------------------------------------------------------------
Ranking within a group: a lexicographic sort, not a single blended
composite with invented weights -- the same "no arbitrary relative
weighting between two real-but-differently-scaled numbers" discipline
`sim.api.draft_autopsy_view` already established when it explicitly
rejected blending `value_gap` and an ADP-relative "steal" metric into one
score (see docs/decisions.md Phase 7).

    within-group sort key (descending): expected_playable_points

`expected_playable_points = mean_points_per_game * opportunity_score` is
not an invented composite either -- it is the exact same "mean times a
[0, 1] probability-like ratio" pattern `sim.api.roster_view._team_risk_rating`
already established (`sum(mean_i * availability_i)`) for "how many of this
player's projected points are actually realistic to bank," just applied to
one candidate instead of summed across a roster.

--------------------------------------------------------------------------
Why this doesn't call `simulate_seasons()`:

A legitimate extension was considered -- "how much would adding this player
change my title odds," via the exact same `roster_overrides` mechanism
`sim.api.lineup_optimizer_view` already uses for its single-slot-swap
search -- and set aside for this phase, not rejected as illegitimate.
`lineup_optimizer_view` makes that tractable by scoping to ~14-19 candidates
for ONE already-rostered team. Waiver intelligence's candidate pool is up to
300 free agents, and a meaningful "impact if added" number needs BOTH a
roster-construction decision (who would this player replace, or does a
bench slot exist) AND a per-candidate `simulate_seasons()` call -- either
re-deriving `lineup_optimizer_view`'s entire single-slot-swap search for
every one of up to 300 free agents (a multiplicative blow-up
`lineup_optimizer_view`'s own docstring already measured single-team costs
for), or arbitrarily assuming a specific bench slot gets replaced (which
would be inventing a roster decision this module has no real basis to make
on the user's behalf). Kept simple instead, per PLAN.md's own bare-minimum
ask (opportunity/availability/league-fit/competition, no simulated-odds
requirement) -- revisit only if a future phase wants to cost that
computation deliberately, e.g. for a single user-selected candidate rather
than the full ranked list.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg

from ingest.parse import parse_player_pool, parse_scoring_table, parse_teams
from ingest.slots import DEFAULT_POSITION_NAMES, NON_STARTING_SLOTS
from sim.api.params_loader import load_raw_payload
from sim.engine import PlayerParams
from sim.params.derive import derive_player_params_pool
from sim.params.variance import fit_position_cv

# injuryStatus values that mean "not realistically playing right now" --
# forces opportunity_score to the natural floor of its own [0, 1] range
# (see module docstring, Signal 1). QUESTIONABLE is deliberately NOT here.
_HARD_OUT_STATUSES = frozenset({"OUT", "INJURY_RESERVE"})

# "More than half the time" -- the one natural, non-arbitrary boundary
# point on the [0, 1] start-rate ratio scale, used only to choose which of
# two mirror-image sentences the reasoning text prints. Not a fitted value
# and not part of any score.
_SETTLED_ROLE_THRESHOLD = 0.5

# The four positions with a real bench-depth strategy in this league (and,
# structurally, in ESPN-style fantasy rosters generally) -- the same four
# sim.api.draft_autopsy_view already treats as worth a positional grade
# (_GRADED_POSITIONS there). K and D/ST are excluded from League fit /
# Competition -- see module docstring's Signal 3/4 "caught before shipping"
# note for why (bench depth at those two positions is 0, or nearly so, for
# literally every team in this real league -- a constant, not a
# per-team differentiator).
_BENCH_DEPTH_RELEVANT_POSITIONS = frozenset({"QB", "RB", "WR", "TE"})

# Canonical display order for position groups -- QB/RB/WR/TE (the positions
# with a real bench-depth decision) first, K/D-ST (streaming-only, per the
# module docstring) last. Purely a presentation choice, not a scoring input.
_POSITION_DISPLAY_ORDER: tuple[str, ...] = ("QB", "RB", "WR", "TE", "D/ST", "K")

# How many ranked candidates to return per position group. An editorial
# UI-sizing choice -- the same class of decision as
# sim.api.playoff_planner_view's _FLOOR_PERCENTILE_PCT or
# sim.api.roster_view's 10th/90th percentile band: a presentation decision,
# not a fitted or invented input to any score.
DEFAULT_LIMIT_PER_POSITION = 8

_OWNERSHIP_DATA_NOTE = (
    "Ownership, start-rate, and ADP figures are ESPN's own cross-league consensus data "
    "-- aggregated across many ESPN leagues, not specific to this league's own manager "
    "behavior. League fit and competition are computed entirely from this league's own "
    "real rosters."
)


class UnknownTeamError(ValueError):
    """Raised when `team_id` does not belong to the requested league/season.
    Mapped to HTTP 404 by the route handler, the same class of "this
    specific thing was never real" error every other `sim.api` view module
    raises for the same situation (see `sim.api.lineup_optimizer_view`)."""


@dataclass(frozen=True)
class WaiverCandidate:
    """One free agent, scored on Opportunity and Availability (Signals 1-2)
    for one specific requesting team. League fit and Competition (Signals
    3-4) are position-level facts shared by every candidate at the same
    position -- see `WaiverPositionGroup`, not repeated here."""

    player_id: int
    player_name: str
    injury_status: str | None

    # This player's own projection -- context, never "opportunity" itself
    # (see module docstring's Signal 1).
    mean_points_per_game: float
    # games_projected / 17 (sim.params.availability) -- a season-LONG games-
    # missed forecast, a different notion from opportunity_score's week-to-
    # week role-trust signal; kept as its own clearly-labeled field so the
    # two are never conflated.
    season_availability: float

    # --- Signal 1: Opportunity ---
    percent_owned: float
    percent_started: float
    percent_change: float
    average_draft_position: float | None
    # Raw percent_started / percent_owned, BEFORE the injury-status floor
    # override -- kept alongside opportunity_score for transparency.
    start_rate_ratio: float
    opportunity_score: float

    # --- Signal 2: Availability ---
    # (percent_owned itself, above, IS the availability signal. No separate
    # field needed -- see module docstring's Signal 2.)

    # mean_points_per_game * opportunity_score -- ranks candidates WITHIN
    # this position group only (see module docstring's "Ranking" section
    # for why cross-position comparison would be dishonest).
    expected_playable_points: float

    # A real narrative built from this player's own Opportunity/Availability
    # signals -- never a template filled with just the player's name.
    reasoning: str


@dataclass(frozen=True)
class WaiverPositionGroup:
    """One position's worth of ranked free-agent candidates for one specific
    requesting team, plus the League fit / Competition facts (Signals 3-4)
    that are genuinely position-level, not player-level -- see module
    docstring's "Why this is grouped by position" section."""

    position: str
    # False for K/D-ST -- see module docstring's Signal 3/4 note. When
    # False, team_has_positional_need and rival_teams_with_need are always
    # False/() respectively (bench depth isn't a real decision at this
    # position in this league), but team_bench_depth_at_position still
    # reports the real count.
    bench_depth_relevant: bool
    team_bench_depth_at_position: int
    team_has_positional_need: bool
    team_starters_at_position: tuple[str, ...]
    rival_teams_with_need: tuple[str, ...]
    # A real narrative built from this group's League fit / Competition
    # facts for THIS specific team -- same "server-synthesized, never a
    # template" discipline sim.api.draft_autopsy_view /
    # sim.api.playoff_planner_view already established.
    group_reasoning: str
    candidates: tuple[WaiverCandidate, ...]


@dataclass(frozen=True)
class WaiverIntelligenceResult:
    league_id: int
    season_id: int
    team_id: int
    team_name: str
    ownership_data_note: str
    groups: tuple[WaiverPositionGroup, ...]


def _bench_depth_by_position(
    entries: list[dict[str, Any]], projections_by_id: Mapping[int, Any]
) -> Counter[str]:
    """Count of a team's own bench/IR entries by their real
    `defaultPositionId` label -- the identical rule
    `sim.api.roster_view._positional_concentration` /
    `sim.api.playoff_planner_view`'s inline `bench_position_counts` already
    use, reimplemented here (not imported) for the same reason
    `playoff_planner_view`'s own docstring gives: the rule is shared, the
    local data shape is not."""
    return Counter(
        DEFAULT_POSITION_NAMES.get(projections_by_id[e["playerId"]].default_position_id, "?")
        for e in entries
        if e["lineupSlotId"] in NON_STARTING_SLOTS and e["playerId"] in projections_by_id
    )


def _starters_by_position(
    entries: list[dict[str, Any]], projections_by_id: Mapping[int, Any]
) -> dict[str, list[str]]:
    """Names of a team's own STARTING (non-bench/IR) entries, grouped by
    their real `defaultPositionId` label -- used only for reasoning-text
    context, never for a score."""
    result: dict[str, list[str]] = {}
    for e in entries:
        if e["lineupSlotId"] in NON_STARTING_SLOTS:
            continue
        projection = projections_by_id.get(e["playerId"])
        if projection is None:
            continue
        position = DEFAULT_POSITION_NAMES.get(projection.default_position_id, "?")
        result.setdefault(position, []).append(projection.name)
    return result


def _synthesize_group_reasoning(
    *,
    team_name: str,
    position: str,
    bench_depth_relevant: bool,
    teams_with_bench_depth: int,
    league_team_count: int,
    team_bench_depth_at_position: int,
    team_has_positional_need: bool,
    team_starters_at_position: tuple[str, ...],
    rival_teams_with_need: tuple[str, ...],
) -> str:
    """League fit + Competition (Signals 3-4), synthesized once per position
    for this specific team -- see module docstring for why this is a
    position-level fact, not a per-candidate one."""
    if not bench_depth_relevant:
        return (
            f"Only {teams_with_bench_depth} of {league_team_count} teams in the league carry "
            f"any bench {position} at all, {team_name} included -- roster construction doesn't "
            f"treat {position} depth as a real decision in this league, so these are week-to-week "
            "matchup/streaming calls rather than positional needs or a competitive waiver race."
        )

    if team_has_positional_need:
        starters_txt = (
            ", ".join(team_starters_at_position) if team_starters_at_position else "no one"
        )
        sentence = (
            f"{team_name} has zero bench depth at {position} right now -- your only "
            f"{position} on the roster is {starters_txt}, so this is a real positional need, "
            "not just speculative depth."
        )
    else:
        depth = team_bench_depth_at_position
        sentence = (
            f"{team_name} already carries {depth} bench {position}"
            f"{'s' if depth != 1 else ''}, so this isn't an urgent need for your roster "
            "specifically -- more depth than a must-add."
        )

    if rival_teams_with_need:
        shown = rival_teams_with_need[:3]
        remainder = len(rival_teams_with_need) - len(shown)
        names = ", ".join(shown) + (f", and {remainder} more" if remainder > 0 else "")
        competition = (
            f" {len(rival_teams_with_need)} other team"
            f"{'s' if len(rival_teams_with_need) != 1 else ''} in the league ({names}) also "
            f"{'have' if len(rival_teams_with_need) != 1 else 'has'} zero bench depth at "
            f"{position} -- expect real competition for any waiver claim here."
        )
    else:
        competition = (
            f" No other team in the league is thin at {position} right now, so you likely have "
            "time before a rival claims one of these."
        )

    return sentence + competition


def _synthesize_candidate_reasoning(
    *,
    injury_status: str | None,
    percent_owned: float,
    percent_started: float,
    percent_change: float,
    average_draft_position: float | None,
    mean_points_per_game: float,
    start_rate_ratio: float,
) -> str:
    """Opportunity + Availability (Signals 1-2), synthesized per candidate --
    the two signals that are genuinely player-specific, not position-level."""
    sentences: list[str] = []

    if injury_status in _HARD_OUT_STATUSES:
        label = injury_status.replace("_", " ").title() if injury_status else "unavailable"
        sentences.append(
            f"Currently listed {label} -- no realistic playing time right now regardless of "
            "role, so this is a stash-and-monitor add, not an immediate lineup answer."
        )
    else:
        role_clause = (
            "a strong signal his role is settled"
            if start_rate_ratio >= _SETTLED_ROLE_THRESHOLD
            else "more of a speculative stash across the league than a trusted starter"
        )
        questionable_clause = (
            " He's currently listed Questionable, which adds some week-to-week uncertainty on "
            "top of that."
            if injury_status == "QUESTIONABLE"
            else ""
        )
        sentences.append(
            f"Among ESPN managers who already roster him, {percent_started:.0f}% actually start "
            f"him against {percent_owned:.0f}% who simply own him -- a "
            f"{start_rate_ratio * 100:.0f}% start rate among owners, {role_clause}."
            f"{questionable_clause} Projects for {mean_points_per_game:.1f} pts/game on the "
            "league's own scoring once he's on the field."
        )

    change_clause = (
        f", {'up' if percent_change > 0 else 'down'} {abs(percent_change):.1f} points recently "
        "across ESPN"
        if abs(percent_change) >= 0.01
        else ""
    )
    adp_clause = (
        f" His preseason average draft position across ESPN was {average_draft_position:.0f}."
        if average_draft_position is not None
        else ""
    )
    availability_sentence = (
        f"Owned in {percent_owned:.1f}% of ESPN leagues overall{change_clause} -- unusually "
        "high for a name still on this league's wire."
        if percent_owned >= 50.0
        else f"Owned in only {percent_owned:.1f}% of ESPN leagues overall{change_clause} -- a "
        "true deep-league/streaming-caliber add most leagues won't bother with."
    )
    sentences.append(availability_sentence + adp_clause)

    return " ".join(sentences)


def compute_waiver_intelligence(
    conn: psycopg.Connection[Any],
    league_id: int,
    season_id: int,
    team_id: int,
    *,
    limit_per_position: int = DEFAULT_LIMIT_PER_POSITION,
) -> WaiverIntelligenceResult:
    """Build the ranked, position-grouped Waiver Intelligence list for one
    team. Raises `LeagueNotIngestedError` (via `load_raw_payload`) if the
    league/season was never ingested, `UnknownTeamError` if `team_id` is not
    a real team in this league/season, and `sim.params.errors.ParamsError`
    subclasses if a free agent's position has no fitted variance parameter
    -- propagated unchanged, same convention as every other `sim.api` view
    module.
    """
    raw = load_raw_payload(conn, league_id, season_id)
    return _compute_from_raw(raw, league_id, season_id, team_id, limit_per_position=limit_per_position)


def _compute_from_raw(
    raw: dict[str, Any],
    league_id: int,
    season_id: int,
    team_id: int,
    *,
    limit_per_position: int,
) -> WaiverIntelligenceResult:
    """The actual computation, factored out from `compute_waiver_intelligence`
    so it can be exercised directly against an in-memory fixture dict (no
    Postgres needed) -- same fast-unit-tests-plus-thin-integration-test
    split every other `sim.api` view module already established."""
    scoring_table = parse_scoring_table(raw)
    player_pool, _skipped = parse_player_pool(raw, scoring_table)
    projections_by_id = {p.player_id: p for p in player_pool}
    position_cv = fit_position_cv(raw)
    players_by_id: dict[int, PlayerParams] = derive_player_params_pool(player_pool, position_cv)

    teams = parse_teams(raw)
    team_by_id = {t.team_id: t for t in teams}
    if team_id not in team_by_id:
        raise UnknownTeamError(
            f"team_id={team_id} is not a team in league_id={league_id} season_id={season_id}"
        )
    team_name = team_by_id[team_id].name

    raw_teams_by_id = {t["id"]: t for t in raw["teams"]}

    own_entries = raw_teams_by_id[team_id].get("roster", {}).get("entries", [])
    own_bench_depth = _bench_depth_by_position(own_entries, projections_by_id)
    own_starters = _starters_by_position(own_entries, projections_by_id)

    # Per-position: which rival team names have zero bench depth (Signal 4),
    # and how many teams total (including this one) carry any bench depth
    # at all -- the real number the K/D-ST "not a real decision here"
    # sentence cites.
    rival_names_with_need_by_position: dict[str, list[str]] = {}
    teams_with_bench_depth_by_position: Counter[str] = Counter()
    for team in teams:
        entries = raw_teams_by_id[team.team_id].get("roster", {}).get("entries", [])
        team_bench_counts = _bench_depth_by_position(entries, projections_by_id)
        for position in DEFAULT_POSITION_NAMES.values():
            if team_bench_counts[position] > 0:
                teams_with_bench_depth_by_position[position] += 1
            if team.team_id != team_id and team_bench_counts[position] == 0:
                rival_names_with_need_by_position.setdefault(position, []).append(team.name)

    free_agents = raw.get("_freeAgents")
    if not isinstance(free_agents, list):
        raise ValueError("fixture has no '_freeAgents' list")

    candidates_by_position: dict[str, list[WaiverCandidate]] = {}
    for entry in free_agents:
        raw_player = entry["player"]
        player_id = raw_player["id"]
        projection = projections_by_id.get(player_id)
        params = players_by_id.get(player_id)
        if projection is None or params is None:
            # No usable ESPN projection for this free agent (see
            # ingest.parse.parse_player_pool's SkippedPlayer handling) --
            # nothing to score them on, so they're excluded rather than
            # given a fabricated number.
            continue

        position = DEFAULT_POSITION_NAMES.get(projection.default_position_id, "?")
        injury_status = raw_player.get("injuryStatus")

        ownership = raw_player.get("ownership") or {}
        percent_owned = float(ownership.get("percentOwned") or 0.0)
        percent_started = float(ownership.get("percentStarted") or 0.0)
        percent_change = float(ownership.get("percentChange") or 0.0)
        adp_raw = ownership.get("averageDraftPosition")
        average_draft_position = float(adp_raw) if isinstance(adp_raw, int | float) else None

        start_rate_ratio = (
            min(1.0, max(0.0, percent_started / percent_owned)) if percent_owned > 0 else 0.0
        )
        opportunity_score = 0.0 if injury_status in _HARD_OUT_STATUSES else start_rate_ratio
        expected_playable_points = params.mean * opportunity_score

        reasoning = _synthesize_candidate_reasoning(
            injury_status=injury_status,
            percent_owned=percent_owned,
            percent_started=percent_started,
            percent_change=percent_change,
            average_draft_position=average_draft_position,
            mean_points_per_game=params.mean,
            start_rate_ratio=start_rate_ratio,
        )

        candidates_by_position.setdefault(position, []).append(
            WaiverCandidate(
                player_id=player_id,
                player_name=projection.name,
                injury_status=injury_status,
                mean_points_per_game=params.mean,
                season_availability=params.availability,
                percent_owned=percent_owned,
                percent_started=percent_started,
                percent_change=percent_change,
                average_draft_position=average_draft_position,
                start_rate_ratio=start_rate_ratio,
                opportunity_score=opportunity_score,
                expected_playable_points=expected_playable_points,
                reasoning=reasoning,
            )
        )

    groups: list[WaiverPositionGroup] = []
    for position in _POSITION_DISPLAY_ORDER:
        position_candidates = candidates_by_position.get(position, [])
        if not position_candidates:
            continue
        position_candidates.sort(key=lambda c: c.expected_playable_points, reverse=True)

        bench_depth_relevant = position in _BENCH_DEPTH_RELEVANT_POSITIONS
        bench_depth = own_bench_depth[position]
        has_need = bench_depth_relevant and bench_depth == 0
        rival_names = (
            tuple(rival_names_with_need_by_position.get(position, ())) if bench_depth_relevant else ()
        )

        group_reasoning = _synthesize_group_reasoning(
            team_name=team_name,
            position=position,
            bench_depth_relevant=bench_depth_relevant,
            teams_with_bench_depth=teams_with_bench_depth_by_position[position],
            league_team_count=len(teams),
            team_bench_depth_at_position=bench_depth,
            team_has_positional_need=has_need,
            team_starters_at_position=tuple(own_starters.get(position, ())),
            rival_teams_with_need=rival_names,
        )

        groups.append(
            WaiverPositionGroup(
                position=position,
                bench_depth_relevant=bench_depth_relevant,
                team_bench_depth_at_position=bench_depth,
                team_has_positional_need=has_need,
                team_starters_at_position=tuple(own_starters.get(position, ())),
                rival_teams_with_need=rival_names,
                group_reasoning=group_reasoning,
                candidates=tuple(position_candidates[:limit_per_position]),
            )
        )

    groups.sort(
        key=lambda g: (
            g.bench_depth_relevant and g.team_has_positional_need,
            g.bench_depth_relevant,
        ),
        reverse=True,
    )

    return WaiverIntelligenceResult(
        league_id=league_id,
        season_id=season_id,
        team_id=team_id,
        team_name=team_name,
        ownership_data_note=_OWNERSHIP_DATA_NOTE,
        groups=tuple(groups),
    )
