"""Build and ingest a SYNTHETIC validation league into Postgres -- for local
Phase 4 API verification only. NOT a real forecast for the user's actual
league, and NOT a substitute for ingesting the real league once it is
drafted.

Why this exists: `fixtures/league_raw_2026.json` is genuinely pre-draft
(`draftDetail.drafted` is `False`, every real team's `roster.entries` is
empty -- see docs/decisions.md, Phase 1). `ingest.parse.build_team_params`
correctly refuses to fabricate a lineup for any of this league's real teams.
Phase 4's done criterion ("curl against a locally ingested league returns
title odds identical to a direct engine call with the same seed") therefore
needs *some* ingested league with real `TeamParams` to test against. Phase 2
already hit this exact blocker and the project owner approved a synthetic
mock league (`sim/params/mock_rosters.py`) as the resolution -- this script
applies the same precedent to Phase 4, rather than re-litigating it.

What it does: runs the identical snake draft `sim.params.mock_rosters`
already uses (via the shared `draft_mock_rosters` function -- not a second
draft implementation), then assembles a raw payload shaped like ESPN's own
fixture format, with those draft picks written into `teams[].roster.entries`
so it can be ingested through the *real* DB layer
(`ingest.db.ingest_league`) exactly as a real post-draft fixture would be.
Every individual player's projection is real (same `_freeAgents` block as
the real fixture); what's synthetic is which players end up on which of 10
fabricated teams, and the league itself is stored under a league_id no real
ESPN league can ever have (see `SYNTHETIC_LEAGUE_ID` below).

Usage:
    python scripts/ingest_synthetic_league.py [--dsn ...] [path/to/fixture.json]
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.db import DEFAULT_DEV_DSN, connect, dsn_from_env, ingest_league, run_migrations
from ingest.models import PlayerProjection
from ingest.parse import FIXTURES_DIR, load_fixture, parse_player_pool, parse_scoring_table
from ingest.slots import BENCH_SLOT_ID
from sim.engine import round_robin_schedule
from sim.params.mock_rosters import draft_mock_rosters

# Real ESPN league ids are always positive. A negative id can never collide
# with a real league, so this is unmistakable in the database at a glance
# (e.g. `SELECT * FROM league WHERE league_id < 0`) -- the same
# "impossible to confuse with a real forecast" discipline
# sim/params/mock_rosters.py applies to its team names.
SYNTHETIC_LEAGUE_ID = -1_990_001

N_MOCK_TEAMS = 10
_MOCK_TEAM_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _draft_bench_entries(
    player_pool: tuple[PlayerProjection, ...],
    drafted_ids: set[int],
    n_mock_teams: int,
    n_bench_slots: int,
) -> list[list[tuple[int, int]]]:
    """Fill each mock team's bench (any position, `BENCH_SLOT_ID`) with the
    same best-remaining-player snake draft `sim.params.mock_rosters` already
    uses for starters -- extends that project-owner-approved fabrication
    category (real players, real projections; only the team grouping is
    fictional) to bench depth, kept local to this script rather than added
    to `sim.params.mock_rosters.draft_mock_rosters` so Phase 2's
    starters-only `build_mock_league` path is completely unaffected (see
    docs/decisions.md Phase 6).

    Bench slots have no position requirement in ESPN's model (unlike a
    starting slot, `eligibleSlots` membership is irrelevant to sitting on the
    bench), so this is a plain best-remaining pick each round, not a
    positional draft. Needed so Alternate-lineup's optimal-vs-actual
    comparison has real bench players to ever swap in -- with zero bench
    depth (this script's pre-Phase-6 behavior) the optimal lineup is
    mechanically always identical to the actual one, which would make that
    what-if type undemonstrable.
    """
    benches: list[list[tuple[int, int]]] = [[] for _ in range(n_mock_teams)]
    for round_index in range(n_bench_slots):
        order = range(n_mock_teams) if round_index % 2 == 0 else range(n_mock_teams - 1, -1, -1)
        for team_index in order:
            candidates = [p for p in player_pool if p.player_id not in drafted_ids]
            if not candidates:
                return benches
            best = max(candidates, key=lambda p: p.mean_points_per_game)
            drafted_ids.add(best.player_id)
            benches[team_index].append((best.player_id, BENCH_SLOT_ID))
    return benches


def build_synthetic_raw_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """A raw-ESPN-shaped payload for a fabricated 10-team league, built from
    `raw`'s real settings and real player pool. Ingestible through
    `ingest.db.ingest_league` exactly like a real fixture.
    """
    scoring_table = parse_scoring_table(raw)
    player_pool, _skipped = parse_player_pool(raw, scoring_table)

    rosters = draft_mock_rosters(raw, player_pool, n_mock_teams=N_MOCK_TEAMS)

    # Bench depth, read from this league's own real rosterSettings rather
    # than hardcoded (settings.rosterSettings.lineupSlotCounts["20"] is 7 in
    # fixtures/league_raw_2026.json) -- see _draft_bench_entries.
    drafted_ids = {player_id for roster in rosters for player_id, _slot_id in roster}
    n_bench_slots = int(
        raw["settings"]["rosterSettings"]["lineupSlotCounts"].get(str(BENCH_SLOT_ID), 0)
    )
    benches = _draft_bench_entries(player_pool, drafted_ids, N_MOCK_TEAMS, n_bench_slots)

    schedule_settings = raw["settings"]["scheduleSettings"]
    n_regular_weeks = schedule_settings["matchupPeriodCount"]

    teams = [
        {
            "id": team_index,
            "name": f"Mock Team {_MOCK_TEAM_LETTERS[team_index]} (SYNTHETIC -- validation only)",
            "abbrev": "SYN",
            "owners": ["synthetic"],
            "roster": {
                "entries": [
                    {"playerId": player_id, "lineupSlotId": lineup_slot_id}
                    for player_id, lineup_slot_id in (*roster, *benches[team_index])
                ]
            },
        }
        for team_index, roster in enumerate(rosters)
    ]

    pairings = round_robin_schedule(n_teams=N_MOCK_TEAMS, n_weeks=n_regular_weeks)
    schedule: list[dict[str, Any]] = []
    match_id = 1
    for week in range(n_regular_weeks):
        for team_index in range(N_MOCK_TEAMS):
            opponent_index = int(pairings[week, team_index])
            if opponent_index <= team_index:
                continue  # each pair appears once per week in the symmetric matrix
            schedule.append(
                {
                    "id": match_id,
                    "matchupPeriodId": week + 1,
                    "winner": "UNDECIDED",
                    "home": {"teamId": team_index},
                    "away": {"teamId": opponent_index},
                }
            )
            match_id += 1

    settings = {
        **raw["settings"],
        "name": "SYNTHETIC validation league (mock draft -- not a real league)",
        "size": N_MOCK_TEAMS,
    }

    return {
        "id": SYNTHETIC_LEAGUE_ID,
        "seasonId": raw["seasonId"],
        "scoringPeriodId": raw.get("scoringPeriodId", 1),
        "segmentId": raw.get("segmentId", 0),
        "gameId": raw.get("gameId"),
        "settings": settings,
        "status": {"teamsJoined": N_MOCK_TEAMS, "isFull": True},
        "draftDetail": {"drafted": True, "picks": []},
        "teams": teams,
        "schedule": schedule,
        # Same real player pool as the source fixture -- every player.py's
        # projection is real ESPN data, not fabricated (see module docstring).
        "_freeAgents": raw["_freeAgents"],
    }


def _main() -> None:
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", nargs="?", default=None, help="Path to a fixture JSON file")
    parser.add_argument("--dsn", default=None, help=f"Postgres DSN (default: {DEFAULT_DEV_DSN})")
    args = parser.parse_args()

    dsn = args.dsn or dsn_from_env("DATABASE_URL", DEFAULT_DEV_DSN)
    fixture_path = Path(args.fixture) if args.fixture else FIXTURES_DIR / "league_raw_2026.json"

    raw = load_fixture(fixture_path)
    synthetic_raw = build_synthetic_raw_payload(raw)

    with connect(dsn) as conn:
        run_migrations(conn)
        summary = ingest_league(conn, synthetic_raw, ingested_at=datetime.now(timezone.utc))
        conn.commit()

    print("=" * 78)
    print("SYNTHETIC validation league ingested -- NOT a real forecast")
    print("=" * 78)
    print(
        f"league_id={summary.league_id} season_id={summary.season_id} into {dsn}\n"
        f"{len(summary.teams)} fabricated teams, {len(summary.player_pool)} real players "
        f"in the pool"
    )


if __name__ == "__main__":
    _main()
