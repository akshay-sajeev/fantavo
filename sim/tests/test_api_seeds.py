"""Tests for sim.api.seeds -- no Postgres required."""

from __future__ import annotations

import numpy as np

from sim.api.seeds import draw_whatif_seed, precompute_seed


def test_precompute_seed_is_deterministic() -> None:
    assert precompute_seed(123, 2026) == precompute_seed(123, 2026)


def test_precompute_seed_differs_for_different_leagues() -> None:
    assert precompute_seed(123, 2026) != precompute_seed(124, 2026)


def test_precompute_seed_differs_for_different_seasons() -> None:
    assert precompute_seed(123, 2026) != precompute_seed(123, 2027)


def test_precompute_seed_handles_a_negative_synthetic_league_id() -> None:
    # scripts/ingest_synthetic_league.SYNTHETIC_LEAGUE_ID is negative by
    # design (no real ESPN league id can be) -- the seed formula must still
    # produce a valid, non-negative np.random.default_rng() seed for it.
    seed = precompute_seed(-1_990_001, 2026)
    assert isinstance(seed, int)
    assert seed >= 0
    np.random.default_rng(seed)  # must not raise


def test_precompute_seed_is_a_valid_generator_seed_for_large_ids() -> None:
    seed = precompute_seed(999_999_999_999, 2099)
    np.random.default_rng(seed)  # must not raise


def test_draw_whatif_seed_returns_distinct_values() -> None:
    seeds = {draw_whatif_seed() for _ in range(20)}
    assert len(seeds) == 20


def test_draw_whatif_seed_is_a_valid_generator_seed() -> None:
    for _ in range(5):
        np.random.default_rng(draw_whatif_seed())  # must not raise
