"""Print a league summary parsed from the saved fixture.

Usage:
    python -m ingest.demo [path/to/fixture.json]

No network calls -- reads fixtures/league_raw_2026.json by default.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ingest.errors import RosterNotAvailableError
from ingest.models import LeagueSummary
from ingest.parse import FIXTURES_DIR, build_team_params, load_fixture, parse_league_summary
from sim.engine import PlayerParams


def _print_summary(raw: dict[str, Any], summary: LeagueSummary) -> None:
    print(f"{summary.name}  (league {summary.league_id}, season {summary.season_id})")
    print(
        f"  {summary.teams_joined}/{summary.size} teams joined"
        f"{'' if summary.is_full else ' (not full)'}"
    )
    print(f"  draft complete: {summary.is_drafted}")
    print(f"  points per reception: {summary.points_per_reception}")
    print(
        f"  {summary.n_regular_weeks} regular-season weeks, "
        f"{summary.n_playoff_teams} playoff teams"
    )
    print(
        f"  schedule: {summary.schedule.n_regular_weeks} x "
        f"{summary.schedule.n_teams} pairing array parsed, team order "
        f"{summary.schedule.team_order}"
    )

    print()
    print("  Teams:")
    for team in summary.teams:
        owner_note = "" if team.has_owner else "  (unclaimed slot)"
        print(f"    {team.team_id:>2}  {team.name}{owner_note}")

    print()
    print(f"  Player pool: {len(summary.player_pool)} players with a usable projection")
    if summary.skipped_players:
        print(f"  Skipped ({len(summary.skipped_players)}):")
        for skipped in summary.skipped_players:
            print(f"    {skipped.name}: {skipped.reason}")

    top = sorted(summary.player_pool, key=lambda proj: -proj.mean_points_per_game)[:10]
    print()
    print("  Top 10 by projected points per game:")
    for proj in top:
        print(
            f"    {proj.name:<24} {proj.mean_points_per_game:5.1f} pts/gm  "
            f"({proj.games_projected} gm projected, {proj.percent_owned:.1f}% owned)"
        )

    print()
    print("  Team rosters:")
    # sd/availability haven't been decided yet (see docs/decisions.md), so
    # there is no PlayerParams to hand build_team_params -- this just
    # exercises and reports the roster-availability check.
    players_by_id: dict[int, PlayerParams] = {}
    for team in summary.teams:
        try:
            build_team_params(raw, team.team_id, players_by_id)
        except RosterNotAvailableError as exc:
            print(f"    team {team.team_id}: {exc}")


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURES_DIR / "league_raw_2026.json"
    raw = load_fixture(path)
    summary = parse_league_summary(raw)
    _print_summary(raw, summary)


if __name__ == "__main__":
    main()
