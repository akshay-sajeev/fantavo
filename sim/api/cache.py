"""JSONB-backed simulation-result cache in Postgres -- the "cached
precomputed results" `GET /league/{id}/simulation` serves.

Follows the same JSONB-alongside-normalized-tables convention Phase 3 used
for raw ESPN payloads (CLAUDE.md's storage convention, `db/migrations/
0002_create_simulation_cache.sql`): one row per (league_id, season_id) in
`simulation_cache`, upserted in place by the scheduled precompute job.
`computed_at` is a required, explicit argument for the same reason
`ingest_league`'s `ingested_at` is (see `ingest/db.py`) -- an implicit
`now()` would make two precompute runs with a fixed seed produce rows that
differ only by wall-clock time, defeating any deterministic test written
against this table.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from sim.engine import SimulationResult


@dataclass(frozen=True)
class CachedSimulation:
    league_id: int
    season_id: int
    n_sims: int
    seed: int
    computed_at: datetime
    result: dict[str, Any]  # JSON-shaped SimulationResult -- see serialize_result


def serialize_result(result: SimulationResult) -> dict[str, Any]:
    """`SimulationResult` -> a plain JSON-able dict.

    Keeps every per-team distribution (`finish_distribution` included)
    rather than collapsing to a single scalar -- CLAUDE.md's "distributions,
    not point estimates" rule applies to this response shape just as much as
    to the sampling inside `simulate_seasons()` itself. A 21% title chance
    with a wide finish distribution is a different result than 21% with a
    narrow one, and this shape lets a caller tell the difference.
    """
    return {
        "n_sims": result.n_sims,
        "n_places": int(result.finish_distribution.shape[1]),
        "teams": [
            {
                "team_id": team_id,
                "team_name": team_name,
                "title_probability": float(result.won_title[i]),
                "playoff_probability": float(result.made_playoffs[i]),
                "reached_final_probability": float(result.reached_final[i]),
                "mean_wins": float(result.mean_wins[i]),
                "mean_points_for": float(result.mean_points_for[i]),
                "finish_distribution": [float(x) for x in result.finish_distribution[i]],
            }
            for i, (team_id, team_name) in enumerate(
                zip(result.team_ids, result.team_names, strict=True)
            )
        ],
        "metadata": result.metadata,
    }


def write_cached_simulation(
    conn: psycopg.Connection[Any],
    league_id: int,
    season_id: int,
    result: SimulationResult,
    seed: int,
    computed_at: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO simulation_cache (league_id, season_id, n_sims, seed, computed_at, result)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (league_id, season_id) DO UPDATE SET
            n_sims = EXCLUDED.n_sims,
            seed = EXCLUDED.seed,
            computed_at = EXCLUDED.computed_at,
            result = EXCLUDED.result
        """,
        (
            league_id,
            season_id,
            result.n_sims,
            seed,
            computed_at,
            json.dumps(serialize_result(result)),
        ),
    )


def read_cached_simulation(
    conn: psycopg.Connection[Any], league_id: int, season_id: int
) -> CachedSimulation | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT league_id, season_id, n_sims, seed, computed_at, result
            FROM simulation_cache
            WHERE league_id = %s AND season_id = %s
            """,
            (league_id, season_id),
        )
        row = cur.fetchone()
    if row is None:
        return None
    league_id_, season_id_, n_sims, seed, computed_at, result = row
    return CachedSimulation(
        league_id=league_id_,
        season_id=season_id_,
        n_sims=n_sims,
        seed=seed,
        computed_at=computed_at,
        result=result,
    )
