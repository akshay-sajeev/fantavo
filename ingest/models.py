"""Typed intermediate representations between a raw ESPN fixture and the
engine's LeagueParams / TeamParams / PlayerParams.

These are deliberately *not* the engine dataclasses. A PlayerProjection has
a mean derived from ESPN's projections but no `sd` or `availability` --
those are decided in a later step (see ingest/projections.py, added after
the sd/availability derivation is chosen) because a plausible-looking
placeholder would violate CLAUDE.md's "no invented numbers" rule.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True)
class RawTeam:
    """A team slot in the league, whether or not it has been claimed or
    drafted yet."""

    team_id: int
    name: str
    abbrev: str
    has_owner: bool


@dataclass(frozen=True)
class PlayerProjection:
    """A player's projected mean scoring rate under this league's own
    scoring settings, plus enough metadata to later assign availability.

    `mean_points_per_game` comes from dividing `season_total` (computed by
    ingest.scoring from the league's own scoringSettings) by
    `games_projected` (the games count ESPN's own projection assumed,
    recovered from appliedTotal / appliedAverage -- not an invented number,
    just ESPN's own denominator made explicit).
    """

    player_id: int
    name: str
    default_position_id: int
    eligible_slots: frozenset[int]
    season_total: float
    games_projected: int
    mean_points_per_game: float
    percent_owned: float
    percent_started: float

    def __post_init__(self) -> None:
        if self.games_projected <= 0:
            raise ValueError(
                f"player {self.player_id} ({self.name}): games_projected must be > 0, "
                f"got {self.games_projected}"
            )
        if self.mean_points_per_game <= 0:
            raise ValueError(
                f"player {self.player_id} ({self.name}): mean_points_per_game must be "
                f"> 0, got {self.mean_points_per_game}"
            )


@dataclass(frozen=True)
class SkippedPlayer:
    """A player in the pool who could not be given a usable projection."""

    player_id: int
    name: str
    reason: str


@dataclass(frozen=True)
class LeagueSchedule:
    """A parsed regular-season schedule, indexed by position in `team_order`
    rather than by ESPN team_id.

    `pairings` has shape (n_regular_weeks, n_teams) and is exactly the array
    LeagueParams.schedule expects: pairings[w, t] is the array-index of the
    team that team t plays in week w.
    """

    team_order: tuple[int, ...]  # ESPN team_id at each array index
    pairings: npt.NDArray[np.int64]

    @property
    def n_teams(self) -> int:
        return len(self.team_order)

    @property
    def n_regular_weeks(self) -> int:
        return int(self.pairings.shape[0])


@dataclass(frozen=True)
class LeagueSummary:
    """Everything ingest could parse from one fixture.

    Deliberately holds RawTeam metadata rather than sim.engine.TeamParams:
    building a TeamParams requires a drafted roster, which this league may
    not have yet (see ingest.parse.build_team_params, which raises
    RosterNotAvailableError rather than fabricate one).
    """

    league_id: int
    name: str
    season_id: int
    size: int
    teams_joined: int
    is_full: bool
    is_drafted: bool
    n_regular_weeks: int
    n_playoff_teams: int
    points_per_reception: float
    teams: tuple[RawTeam, ...]
    schedule: LeagueSchedule
    player_pool: tuple[PlayerProjection, ...]
    skipped_players: tuple[SkippedPlayer, ...]
