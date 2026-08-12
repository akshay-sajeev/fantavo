"""Read-only view of a league's regular-season schedule, backing
`GET /league/{id}/schedule`.

Built directly from the normalized `league`, `team` and `matchup` tables
Phase 3 already populates -- `matchup_period_id` / `home_team_id` /
`away_team_id` / `winner` are already extracted normalized columns
(`db/migrations/0001_create_schema.sql`), so this needs no raw-payload
reparsing the way `params_loader`/`roster_view` do.

"Current week": this app has no weekly-actuals ingestion yet (that is
Phase 9's job), and a freshly-drafted or synthetic league has every
matchup's `winner` set to ESPN's own `"UNDECIDED"` sentinel (see
`scripts/ingest_synthetic_league.py`). Rather than invent a "current week"
number from wall-clock time or fabricate a game result, `current_week` is
defined purely from data already in the `matchup` table: the first week (by
`matchup_period_id`) containing at least one matchup whose `winner` is not
a decided value. If every regular-season week already has a decided winner
for every matchup, `current_week` is `None` -- there is no "current" week
because the ingested schedule is entirely in the past. This can never
report a week as "current" that the matchup table doesn't actually still
show as undecided.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import psycopg

from sim.api.params_loader import LeagueNotIngestedError

# ESPN's own decided-outcome values for `matchup.winner`. Anything else
# (None, or the "UNDECIDED" sentinel ESPN itself uses pre-game) counts as
# not yet decided.
_DECIDED_WINNERS = frozenset({"HOME", "AWAY", "TIE"})


@dataclass(frozen=True)
class ScheduledMatchup:
    week: int
    home_team_id: int | None
    home_team_name: str | None
    away_team_id: int | None
    away_team_name: str | None
    winner: str | None


@dataclass(frozen=True)
class ScheduleView:
    n_regular_weeks: int
    current_week: int | None
    weeks: tuple[tuple[ScheduledMatchup, ...], ...]  # weeks[0] is week 1


def load_schedule(conn: psycopg.Connection[Any], league_id: int, season_id: int) -> ScheduleView:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT n_regular_weeks FROM league WHERE league_id = %s AND season_id = %s",
            (league_id, season_id),
        )
        row = cur.fetchone()
    if row is None:
        raise LeagueNotIngestedError(
            f"no ingested league found for league_id={league_id}, season_id={season_id} "
            "-- ingest it first; refusing to fabricate a schedule"
        )
    n_regular_weeks: int = row[0]

    with conn.cursor() as cur:
        cur.execute(
            "SELECT team_id, name FROM team WHERE league_id = %s AND season_id = %s",
            (league_id, season_id),
        )
        team_names: dict[int, str] = dict(cur.fetchall())

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT matchup_period_id, home_team_id, away_team_id, winner
            FROM matchup
            WHERE league_id = %s AND season_id = %s AND matchup_period_id <= %s
            ORDER BY matchup_period_id, home_team_id
            """,
            (league_id, season_id, n_regular_weeks),
        )
        rows = cur.fetchall()

    by_week: dict[int, list[ScheduledMatchup]] = defaultdict(list)
    for matchup_period_id, home_id, away_id, winner in rows:
        by_week[matchup_period_id].append(
            ScheduledMatchup(
                week=matchup_period_id,
                home_team_id=home_id,
                home_team_name=team_names.get(home_id) if home_id is not None else None,
                away_team_id=away_id,
                away_team_name=team_names.get(away_id) if away_id is not None else None,
                winner=winner,
            )
        )

    current_week: int | None = None
    for week in range(1, n_regular_weeks + 1):
        week_matchups = by_week.get(week, [])
        if any(m.winner not in _DECIDED_WINNERS for m in week_matchups):
            current_week = week
            break

    weeks = tuple(tuple(by_week.get(week, [])) for week in range(1, n_regular_weeks + 1))
    return ScheduleView(n_regular_weeks=n_regular_weeks, current_week=current_week, weeks=weeks)
