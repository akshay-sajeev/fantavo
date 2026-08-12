"""Tests for sim.api.precompute against a real local Postgres instance.

test_precompute_league_matches_a_direct_engine_call_with_the_same_seed is
Phase 4's literal done criterion, checked at the Python level (the HTTP-level
version of the same check lives in sim/tests/test_api_app.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import psycopg

from ingest.db import ingest_league
from sim.api.cache import read_cached_simulation
from sim.api.params_loader import load_league
from sim.api.precompute import precompute_all_leagues, precompute_league
from sim.api.seeds import precompute_seed
from sim.engine import simulate_seasons


def test_precompute_league_matches_a_direct_engine_call_with_the_same_seed(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    season_id = raw_fixture["seasonId"]
    computed_at = datetime(2026, 6, 1, tzinfo=timezone.utc)

    used_seed = precompute_league(pg_conn, synthetic_league_id, season_id, computed_at, n_sims=500)
    pg_conn.commit()
    assert used_seed == precompute_seed(synthetic_league_id, season_id)

    cached = read_cached_simulation(pg_conn, synthetic_league_id, season_id)
    assert cached is not None

    # A fresh direct simulate_seasons() call, loading params the same way,
    # with the same seed, must reproduce the exact cached title odds.
    loaded = load_league(pg_conn, synthetic_league_id, season_id)
    direct_result = simulate_seasons(
        loaded.league, n_sims=500, rng=np.random.default_rng(used_seed)
    )

    cached_title_odds = {t["team_id"]: t["title_probability"] for t in cached.result["teams"]}
    direct_title_odds = {
        team_id: float(prob)
        for team_id, prob in zip(direct_result.team_ids, direct_result.won_title, strict=True)
    }
    assert cached_title_odds == direct_title_odds

    cached_finish = {t["team_id"]: t["finish_distribution"] for t in cached.result["teams"]}
    direct_finish = {
        team_id: [float(x) for x in direct_result.finish_distribution[i]]
        for i, team_id in enumerate(direct_result.team_ids)
    }
    assert cached_finish == direct_finish


def test_precompute_all_leagues_skips_a_league_with_no_drafted_roster(
    pg_conn: psycopg.Connection[Any], raw_fixture: dict[str, Any]
) -> None:
    """fixtures/league_raw_2026.json's real league is genuinely pre-draft
    (see docs/decisions.md Phase 1) -- precompute_all_leagues must skip it
    (RosterNotAvailableError), not raise, and not cache anything for it."""
    ingest_league(pg_conn, raw_fixture, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    pg_conn.commit()

    precompute_all_leagues(pg_conn, computed_at=datetime(2026, 6, 1, tzinfo=timezone.utc))
    pg_conn.commit()

    cached = read_cached_simulation(pg_conn, raw_fixture["id"], raw_fixture["seasonId"])
    assert cached is None


def test_precompute_all_leagues_caches_the_synthetic_league(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    precompute_all_leagues(pg_conn, computed_at=datetime(2026, 6, 1, tzinfo=timezone.utc))
    pg_conn.commit()

    cached = read_cached_simulation(pg_conn, synthetic_league_id, raw_fixture["seasonId"])
    assert cached is not None
    assert cached.n_sims > 0
    assert len(cached.result["teams"]) == 10
