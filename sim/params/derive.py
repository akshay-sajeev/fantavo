"""Combine a real ESPN projection with fitted variance and derived
availability into an engine-ready `sim.engine.PlayerParams`.

This is the join point between `ingest.models.PlayerProjection` (Phase 1's
output: a real, ESPN-sourced mean and nothing else) and
`sim.engine.PlayerParams` (what the simulator needs: mean, sd, availability).
Every input here is either real data or a documented fitted/assumed
parameter -- see sim/params/variance.py and sim/params/availability.py for
where sd and availability actually come from.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ingest.models import PlayerProjection
from sim.engine import PlayerParams
from sim.params.availability import derive_availability
from sim.params.errors import MissingVarianceParameterError


def derive_player_params(
    projection: PlayerProjection, position_cv: Mapping[int, float]
) -> PlayerParams:
    """Build one player's PlayerParams from a real projection and a fitted
    position -> coefficient-of-variation table (see variance.fit_position_cv).

    Raises MissingVarianceParameterError if this player's position has no
    fitted CV -- refuses to fall back to a borrowed or guessed value.
    """
    cv = position_cv.get(projection.default_position_id)
    if cv is None:
        raise MissingVarianceParameterError(
            f"no fitted coefficient of variation for defaultPositionId="
            f"{projection.default_position_id} ({projection.name}); cannot "
            "derive sd without one"
        )

    mean = projection.mean_points_per_game
    return PlayerParams(
        player_id=projection.player_id,
        mean=mean,
        sd=mean * cv,
        availability=derive_availability(projection.games_projected),
    )


def derive_player_params_pool(
    pool: Iterable[PlayerProjection], position_cv: Mapping[int, float]
) -> dict[int, PlayerParams]:
    """Derive PlayerParams for every projection in `pool`, keyed by
    player_id -- the shape `ingest.parse.build_team_params` expects for its
    `players_by_id` argument.
    """
    return {
        projection.player_id: derive_player_params(projection, position_cv)
        for projection in pool
    }
