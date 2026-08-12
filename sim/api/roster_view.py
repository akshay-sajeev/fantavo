"""Read-only view of team rosters and per-player risk metrics, backing
`GET /league/{id}/roster`.

Additive to Phase 4's `params_loader`: reuses the *identical*
`ingest.parse` + `sim.params` pipeline `load_league` already established
(same scoring table, same player pool, same fitted position CV, same
`derive_player_params_pool`) -- the only difference is that `load_league`
discards player name/position the moment it has a `PlayerParams`, and this
module keeps that metadata alongside the derived params so it can be
rendered.

Two further numbers are computed here that do not exist anywhere else in
the codebase, both deliberately *descriptive*, not a new model:

- floor / ceiling: the 10th / 90th percentile of the exact same per-game
  Gamma(mean, sd) distribution `sim.engine._sample_team_weeks` samples
  from -- same shape/scale formula, imported directly from `sim.engine`
  rather than re-derived, so the two can never silently drift apart. This
  is an analytic property of the distribution the engine already uses to
  simulate seasons, not a second simulation path -- no sampling happens
  here, `scipy.stats.gamma.ppf` is a closed-form inverse CDF.
- team `risk_rating` / `positional_concentration`: plain arithmetic over
  each team's own starters (see the two functions below for the exact,
  fully-spelled-out formula) -- not a fitted parameter, not drawn from
  `simulate_seasons()`, not CLAUDE.md's "one simulation engine" territory
  at all. Kept in this API layer rather than in `sim.engine` (it is a
  display rollup, not a season outcome) and rather than in the frontend
  (CLAUDE.md's "no analytics logic in components" rule) -- this module is
  the one place it is computed, so the frontend only ever renders it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import psycopg
from scipy.stats import gamma as gamma_dist

from ingest.errors import MissingProjectionError
from ingest.parse import parse_player_pool, parse_scoring_table, parse_teams
from ingest.slots import DEFAULT_POSITION_NAMES, NON_STARTING_SLOTS, SLOT_NAMES
from sim.api.params_loader import load_raw_payload
from sim.engine import _gamma_shape_scale
from sim.params.derive import derive_player_params_pool
from sim.params.variance import fit_position_cv

# The percentile band shown as "floor" / "ceiling" for a player's per-game
# score. 10th/90th is a common, easily-explained "reasonable bad week /
# reasonable great week" band -- not a fitted value, just a percentile
# choice, and the same choice for every player so comparisons are
# apples-to-apples.
_FLOOR_PERCENTILE = 0.10
_CEILING_PERCENTILE = 0.90


@dataclass(frozen=True)
class RosterPlayer:
    player_id: int
    name: str
    position: str
    lineup_slot: str
    is_starter: bool
    mean: float
    sd: float
    availability: float
    floor: float
    ceiling: float


@dataclass(frozen=True)
class TeamRosterView:
    team_id: int
    team_name: str
    starters: tuple[RosterPlayer, ...]
    bench: tuple[RosterPlayer, ...]
    # 1 - (sum(mean_i * availability_i) / sum(mean_i)) over starters: the
    # fraction of this team's expected weekly starting points that rides on
    # a player who might not suit up. 0.0 for an empty roster (nothing to
    # be at risk of yet).
    risk_rating: float
    # One entry per starting position with zero same-position bench depth
    # -- see _positional_concentration for the exact rule.
    positional_concentration: tuple[str, ...]


def _floor_ceiling(
    means: npt.NDArray[np.float64], sds: npt.NDArray[np.float64]
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """10th/90th percentile of each player's per-game Gamma(mean, sd)
    distribution -- the same distribution `sim.engine` samples from for
    every simulated week, not a new one. Vectorized over the whole roster
    at once."""
    shape, scale = _gamma_shape_scale(means, sds)
    floor = gamma_dist.ppf(_FLOOR_PERCENTILE, a=shape, scale=scale)
    ceiling = gamma_dist.ppf(_CEILING_PERCENTILE, a=shape, scale=scale)
    return np.asarray(floor, dtype=np.float64), np.asarray(ceiling, dtype=np.float64)


def _team_risk_rating(starters: tuple[RosterPlayer, ...]) -> float:
    total_mean = sum(p.mean for p in starters)
    if total_mean <= 0:
        return 0.0
    expected_available = sum(p.mean * p.availability for p in starters)
    return 1.0 - (expected_available / total_mean)


def _positional_concentration(
    starters: tuple[RosterPlayer, ...], bench: tuple[RosterPlayer, ...]
) -> tuple[str, ...]:
    """Flags a starting position with zero rostered bench depth at that
    same position: if that starter is unavailable in a given week, this
    team has no in-house replacement. A count over already-known position
    labels, not a weighted or fitted score."""
    starter_positions = sorted({p.position for p in starters})
    bench_position_counts = Counter(p.position for p in bench)
    return tuple(
        f"No bench depth at {pos}" for pos in starter_positions if bench_position_counts[pos] == 0
    )


def load_team_rosters(
    conn: psycopg.Connection[Any], league_id: int, season_id: int
) -> tuple[TeamRosterView, ...]:
    """Every team's roster (starters + bench) for an ingested league/season,
    with per-player risk metrics attached. Raises `LeagueNotIngestedError`
    (via `load_raw_payload`) if the league/season was never ingested, and
    `ingest.errors.MissingProjectionError` if a rostered player has no
    usable projection -- the same refusal `ingest.parse.build_team_params`
    already makes for the simulation path, applied here too rather than
    silently hiding an at-risk player from the risk panel.
    """
    raw = load_raw_payload(conn, league_id, season_id)

    scoring_table = parse_scoring_table(raw)
    player_pool, _skipped = parse_player_pool(raw, scoring_table)
    projections_by_id = {p.player_id: p for p in player_pool}
    position_cv = fit_position_cv(raw)
    players_by_id = derive_player_params_pool(player_pool, position_cv)

    teams = parse_teams(raw)
    raw_teams_by_id = {t["id"]: t for t in raw["teams"]}

    views: list[TeamRosterView] = []
    for team in teams:
        raw_team = raw_teams_by_id[team.team_id]
        entries = raw_team.get("roster", {}).get("entries", [])

        entry_player_ids: list[int] = []
        entry_slot_ids: list[int] = []
        for entry in entries:
            player_id = entry["playerId"]
            if player_id not in players_by_id or player_id not in projections_by_id:
                raise MissingProjectionError(
                    f"team {team.team_id} ({team.name}) roster includes player "
                    f"{player_id} with no usable projection -- refusing to render "
                    "it in the roster/risk view rather than hide it silently"
                )
            entry_player_ids.append(player_id)
            entry_slot_ids.append(entry["lineupSlotId"])

        means = np.array([players_by_id[pid].mean for pid in entry_player_ids], dtype=np.float64)
        sds = np.array([players_by_id[pid].sd for pid in entry_player_ids], dtype=np.float64)
        floors, ceilings = _floor_ceiling(means, sds) if entry_player_ids else (
            np.array([]),
            np.array([]),
        )

        starters: list[RosterPlayer] = []
        bench: list[RosterPlayer] = []
        for i, (player_id, slot_id) in enumerate(zip(entry_player_ids, entry_slot_ids, strict=True)):
            projection = projections_by_id[player_id]
            params = players_by_id[player_id]
            is_starter = slot_id not in NON_STARTING_SLOTS
            player = RosterPlayer(
                player_id=player_id,
                name=projection.name,
                position=DEFAULT_POSITION_NAMES.get(projection.default_position_id, "?"),
                lineup_slot=SLOT_NAMES.get(slot_id, str(slot_id)),
                is_starter=is_starter,
                mean=params.mean,
                sd=params.sd,
                availability=params.availability,
                floor=float(floors[i]),
                ceiling=float(ceilings[i]),
            )
            (starters if is_starter else bench).append(player)

        starters_t, bench_t = tuple(starters), tuple(bench)
        views.append(
            TeamRosterView(
                team_id=team.team_id,
                team_name=raw_team.get("name") or f"Team {team.team_id}",
                starters=starters_t,
                bench=bench_t,
                risk_rating=_team_risk_rating(starters_t),
                positional_concentration=_positional_concentration(starters_t, bench_t),
            )
        )

    return tuple(views)
