"""Tests for sim.api.app -- the actual FastAPI routes, driven the same way
curl would drive them (via Starlette's TestClient, an ASGI-level HTTP
client -- not a mocked router).

test_get_simulation_matches_a_direct_engine_call_with_the_same_seed is
Phase 4's literal done criterion: "curl against a locally ingested league
returns title odds identical to a direct engine call with the same seed."
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient

from ingest.db import DEFAULT_TEST_DSN
from sim.api import app as app_module
from sim.api.params_loader import load_league
from sim.api.precompute import precompute_league
from sim.api.seeds import precompute_seed
from sim.engine import simulate_seasons

TEST_DSN = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)


@pytest.fixture()
def client(
    pg_conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """`pg_conn` is requested (even though unused directly) purely so this
    fixture inherits its skip-if-Postgres-unreachable behavior, and so the
    app's own connections (opened per-request from DATABASE_URL, separately
    from `pg_conn`) point at the same already-migrated test database.

    The scheduler is stubbed out: it is simple wiring around
    sim.api.precompute.precompute_all_leagues, which has its own dedicated
    tests (sim/tests/test_api_precompute.py) -- starting a real in-process
    interval thread here would add flakiness risk with no additional
    coverage.
    """
    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    monkeypatch.setattr(app_module, "start_scheduler", lambda dsn=None: None)
    with TestClient(app_module.app) as test_client:
        yield test_client


def test_get_simulation_returns_404_for_an_uningested_league(client: TestClient) -> None:
    response = client.get("/league/424242/simulation")
    assert response.status_code == 404


def test_get_simulation_matches_a_direct_engine_call_with_the_same_seed(
    client: TestClient,
    pg_conn: psycopg.Connection[Any],
    synthetic_league_id: int,
    raw_fixture: dict[str, Any],
) -> None:
    season_id = raw_fixture["seasonId"]
    computed_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    seed = precompute_league(pg_conn, synthetic_league_id, season_id, computed_at, n_sims=500)
    pg_conn.commit()

    response = client.get(
        f"/league/{synthetic_league_id}/simulation", params={"season_id": season_id}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["league_id"] == synthetic_league_id
    assert body["season_id"] == season_id
    assert body["seed"] == seed == precompute_seed(synthetic_league_id, season_id)
    assert len(body["teams"]) == 10

    # The actual done criterion: a direct simulate_seasons() call, loading
    # params the same way, with the same seed, reproduces the API's numbers
    # exactly -- not approximately.
    loaded = load_league(pg_conn, synthetic_league_id, season_id)
    direct_result = simulate_seasons(loaded.league, n_sims=500, rng=np.random.default_rng(seed))

    api_title_odds = {t["team_id"]: t["title_probability"] for t in body["teams"]}
    direct_title_odds = {
        team_id: float(prob)
        for team_id, prob in zip(direct_result.team_ids, direct_result.won_title, strict=True)
    }
    assert api_title_odds == direct_title_odds

    api_finish = {t["team_id"]: t["finish_distribution"] for t in body["teams"]}
    direct_finish = {
        team_id: [float(x) for x in direct_result.finish_distribution[i]]
        for i, team_id in enumerate(direct_result.team_ids)
    }
    assert api_finish == direct_finish


def test_whatif_rejects_an_unknown_player_id(
    client: TestClient, synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    response = client.post(
        f"/league/{synthetic_league_id}/whatif",
        json={
            "season_id": raw_fixture["seasonId"],
            "roster_overrides": {"0": [999_999_999_999]},
            "n_sims": 50,
        },
    )
    assert response.status_code == 422


def test_whatif_rejects_an_uningested_league(raw_fixture: dict[str, Any], client: TestClient) -> None:
    response = client.post(
        "/league/424242/whatif",
        json={"season_id": raw_fixture["seasonId"], "n_sims": 50},
    )
    assert response.status_code == 404


def test_whatif_runs_live_with_an_explicit_seed_and_returns_distributions(
    client: TestClient, synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    response = client.post(
        f"/league/{synthetic_league_id}/whatif",
        json={"season_id": raw_fixture["seasonId"], "n_sims": 50, "seed": 7},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["seed"] == 7
    assert body["n_sims"] == 50
    assert len(body["teams"]) == 10
    for team in body["teams"]:
        assert 0.0 <= team["title_probability"] <= 1.0
        assert len(team["finish_distribution"]) == 7  # n_playoff_teams(6) + 1


def test_whatif_with_an_explicit_seed_is_reproducible(
    client: TestClient, synthetic_league_id: int, raw_fixture: dict[str, Any]
) -> None:
    payload = {"season_id": raw_fixture["seasonId"], "n_sims": 50, "seed": 99}
    first = client.post(f"/league/{synthetic_league_id}/whatif", json=payload).json()
    second = client.post(f"/league/{synthetic_league_id}/whatif", json=payload).json()
    assert first["teams"] == second["teams"]


def test_whatif_applies_a_roster_override(
    client: TestClient,
    pg_conn: psycopg.Connection[Any],
    synthetic_league_id: int,
    raw_fixture: dict[str, Any],
) -> None:
    season_id = raw_fixture["seasonId"]
    loaded = load_league(pg_conn, synthetic_league_id, season_id)
    team_a, team_b = loaded.league.teams[0], loaded.league.teams[1]

    baseline = client.post(
        f"/league/{synthetic_league_id}/whatif",
        json={"season_id": season_id, "n_sims": 2000, "seed": 123},
    ).json()

    # Give team_a team_b's entire roster -- if roster_overrides has no
    # effect this response would be identical to baseline.
    overridden = client.post(
        f"/league/{synthetic_league_id}/whatif",
        json={
            "season_id": season_id,
            "n_sims": 2000,
            "seed": 123,
            "roster_overrides": {team_a.team_id: [p.player_id for p in team_b.starters]},
        },
    ).json()

    assert overridden != baseline
