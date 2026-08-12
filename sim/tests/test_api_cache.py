"""Tests for sim.api.cache against a real local Postgres instance."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import psycopg

from sim.api.cache import read_cached_simulation, serialize_result, write_cached_simulation
from sim.api.params_loader import load_league
from sim.engine import simulate_seasons


def test_read_cached_simulation_returns_none_when_absent(pg_conn: psycopg.Connection[Any]) -> None:
    assert read_cached_simulation(pg_conn, 999_999, 2026) is None


def test_write_and_read_cached_simulation_round_trips(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    season_id = raw_fixture["seasonId"]
    loaded = load_league(pg_conn, synthetic_league_id, season_id)
    result = simulate_seasons(loaded.league, n_sims=200, rng=np.random.default_rng(42))

    computed_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    write_cached_simulation(
        pg_conn, synthetic_league_id, season_id, result, seed=42, computed_at=computed_at
    )
    pg_conn.commit()

    cached = read_cached_simulation(pg_conn, synthetic_league_id, season_id)
    assert cached is not None
    assert cached.league_id == synthetic_league_id
    assert cached.season_id == season_id
    assert cached.seed == 42
    assert cached.n_sims == 200
    assert cached.computed_at == computed_at
    assert cached.result == serialize_result(result)


def test_write_cached_simulation_upserts_in_place(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    """A second precompute run for the same league/season replaces the
    cached row rather than accumulating a second one."""
    season_id = raw_fixture["seasonId"]
    loaded = load_league(pg_conn, synthetic_league_id, season_id)

    first = simulate_seasons(loaded.league, n_sims=100, rng=np.random.default_rng(1))
    write_cached_simulation(
        pg_conn, synthetic_league_id, season_id, first, seed=1,
        computed_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    pg_conn.commit()

    second = simulate_seasons(loaded.league, n_sims=300, rng=np.random.default_rng(2))
    write_cached_simulation(
        pg_conn, synthetic_league_id, season_id, second, seed=2,
        computed_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    pg_conn.commit()

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM simulation_cache WHERE league_id = %s AND season_id = %s",
            (synthetic_league_id, season_id),
        )
        row = cur.fetchone()
    assert row == (1,)

    cached = read_cached_simulation(pg_conn, synthetic_league_id, season_id)
    assert cached is not None
    assert cached.seed == 2
    assert cached.n_sims == 300
