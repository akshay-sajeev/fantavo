"""Tests for sim.api.season_replay_view and
POST /league/{id}/whatif/season-replay.

Focused on the hard invariants that must hold no matter what the one
sampled "actual" season happens to look like (see that module's docstring
for why this feature legitimately samples one realization rather than
reading real weekly scores that don't exist yet), plus the reproducibility
and synthetic-labeling guarantees CLAUDE.md's seeding and "no invented
numbers" rules require.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any

import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import DEFAULT_TEST_DSN
from sim.api import app as app_module
from sim.api.season_replay_view import compute_season_replay
from sim.tests.conftest import ConnectedClient

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)


@pytest.fixture()
def client(
    pg_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    monkeypatch.setattr(app_module, "start_scheduler", lambda dsn=None: None)
    with TestClient(app_module.app) as test_client:
        yield test_client


def test_get_season_replay_returns_403_for_a_league_the_caller_does_not_own(
    connect_as: Callable[[dict[str, Any]], ConnectedClient], raw_fixture: dict[str, Any]
) -> None:
    cc = connect_as(raw_fixture)
    response = cc.client.post(
        "/league/424242/whatif/season-replay", json={}, headers=cc.headers
    )
    assert response.status_code == 403


def test_get_season_replay_requires_auth(client: TestClient) -> None:
    response = client.post("/league/424242/whatif/season-replay", json={})
    assert response.status_code == 401


def test_optimal_lineup_never_scores_less_than_the_actual_lineup(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    season_id = raw_fixture["seasonId"]
    result = compute_season_replay(
        pg_conn, synthetic_league_id, season_id, rng=np.random.default_rng(7)
    )
    assert result.teams
    for team in result.teams:
        # The actual lineup is itself a feasible candidate for the optimal
        # assignment, so the solver can never do worse -- this must hold for
        # every team regardless of what the sampled scores were.
        assert team.optimal_points_for >= team.actual_points_for - 1e-6
        assert team.optimal_wins >= team.actual_wins


def test_neutral_expected_record_sums_to_n_regular_weeks(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    season_id = raw_fixture["seasonId"]
    result = compute_season_replay(
        pg_conn, synthetic_league_id, season_id, rng=np.random.default_rng(11)
    )
    for team in result.teams:
        total = team.neutral_expected_wins + team.neutral_expected_losses + team.neutral_expected_ties
        assert total == pytest.approx(result.n_regular_weeks)
        assert team.actual_wins + team.actual_losses + team.actual_ties == result.n_regular_weeks
        assert team.optimal_wins + team.optimal_losses + team.optimal_ties == result.n_regular_weeks


def test_same_seed_reproduces_the_identical_replay(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    season_id = raw_fixture["seasonId"]
    first = compute_season_replay(
        pg_conn, synthetic_league_id, season_id, rng=np.random.default_rng(99)
    )
    second = compute_season_replay(
        pg_conn, synthetic_league_id, season_id, rng=np.random.default_rng(99)
    )
    assert [t.actual_points_for for t in first.teams] == [t.actual_points_for for t in second.teams]
    assert [t.optimal_wins for t in first.teams] == [t.optimal_wins for t in second.teams]


def test_bench_depth_makes_optimal_lineup_genuinely_different_from_actual(
    pg_conn: psycopg.Connection[Any], synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    """Phase 6 added real bench depth to the synthetic league specifically so
    this what-if is demonstrable (see docs/decisions.md) -- a starters-only
    roster would make optimal == actual by construction, every week, for
    every team. Assert that does NOT happen for at least one team, so this
    regresses if bench depth is ever accidentally removed again.
    """
    season_id = raw_fixture["seasonId"]
    result = compute_season_replay(
        pg_conn, synthetic_league_id, season_id, rng=np.random.default_rng(3)
    )
    assert any(t.optimal_points_for > t.actual_points_for + 1e-6 for t in result.teams)


def test_post_season_replay_matches_a_direct_call_with_the_same_seed(
    client: TestClient,
    pg_conn: psycopg.Connection[Any],
    synthetic_league_id: int,
    raw_fixture: dict[str, Any],
    connect_as: Callable[[dict[str, Any]], ConnectedClient],
) -> None:
    from scripts.ingest_synthetic_league import build_synthetic_raw_payload

    season_id = raw_fixture["seasonId"]
    seed = 555
    direct = compute_season_replay(
        pg_conn, synthetic_league_id, season_id, rng=np.random.default_rng(seed)
    )

    cc = connect_as(build_synthetic_raw_payload(raw_fixture))
    response = cc.client.post(
        f"/league/{synthetic_league_id}/whatif/season-replay",
        json={"season_id": season_id, "seed": seed},
        headers=cc.headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["seed"] == seed
    assert body["synthetic_actual_scores"] is True
    assert "SYNTHETIC" in body["note"]
    assert len(body["teams"]) == len(direct.teams)

    by_id = {t.team_id: t for t in direct.teams}
    for team_out in body["teams"]:
        expected = by_id[team_out["team_id"]]
        assert team_out["actual_wins"] == expected.actual_wins
        assert team_out["optimal_wins"] == expected.optimal_wins
        assert team_out["actual_points_for"] == pytest.approx(expected.actual_points_for)
        assert team_out["optimal_points_for"] == pytest.approx(expected.optimal_points_for)
