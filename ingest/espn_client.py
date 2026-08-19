"""Live ESPN fantasy API client -- the only module allowed to call the real
ESPN API from the always-running service (Auth Phase B's connect flow and
recurring re-ingest job). scripts/fetch_fixture.py (dev-only, manual) also
calls this module rather than duplicating the fetch/merge logic, and layers
its own scrub-and-write-to-/fixtures behavior on top.

CLAUDE.md's "fixtures, not live calls" rule still governs every test in
this repo: `fetch_live_league`'s `transport` parameter is injectable
specifically so tests substitute a fake and never make a real network call.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

import requests

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

VIEWS = [
    "mTeam",
    "mRoster",
    "mMatchup",
    "mMatchupScore",
    "mSettings",
    "mStandings",
    "mDraftDetail",
    "mStatus",
]

FREE_AGENT_FILTER = {
    "players": {
        "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        "limit": 300,
    }
}


class Transport(Protocol):
    def get(self, url: str, **kwargs: Any) -> requests.Response: ...


class EspnFetchError(RuntimeError):
    """Base for every error this module raises. Never includes espn_s2/SWID
    in its message, matching CLAUDE.md's secrets rule."""


class EspnAuthenticationError(EspnFetchError):
    """ESPN returned 401 -- missing, wrong, or expired espn_s2/SWID cookies
    for a private league."""


class EspnLeagueNotFoundError(EspnFetchError):
    """ESPN returned 404 -- no league exists with this league_id/season_id."""


def fetch_live_league(
    league_id: int,
    season_id: int,
    espn_s2: str | None,
    swid: str | None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Fetch and merge every view in VIEWS plus the free-agent pool into the
    single combined payload shape ingest.db.ingest_league expects -- the
    same shape scripts/fetch_fixture.py has always produced.

    `transport` defaults to a real requests.Session(); tests pass a fake
    implementing .get(url, **kwargs) so no network call is ever made in
    this repo's test suite. A failed free-agent-pool call is non-fatal
    (matches scripts/fetch_fixture.py's existing behavior) -- it degrades
    to an empty pool rather than failing the whole connect/re-ingest
    attempt over a secondary endpoint.
    """
    session: Transport = transport if transport is not None else requests.Session()
    cookies = {"espn_s2": espn_s2, "SWID": swid} if espn_s2 and swid else {}

    url = f"{BASE}/seasons/{season_id}/segments/0/leagues/{league_id}"
    params = [("view", v) for v in VIEWS]

    try:
        response = session.get(url, params=params, cookies=cookies, timeout=30)
    except requests.RequestException as exc:
        raise EspnFetchError(f"could not reach ESPN: {type(exc).__name__}") from exc

    if response.status_code == 401:
        raise EspnAuthenticationError(
            "ESPN rejected the request -- for a private league, espn_s2/SWID "
            "must be valid and not expired"
        )
    if response.status_code == 404:
        raise EspnLeagueNotFoundError(
            f"no ESPN league found for league_id={league_id} season_id={season_id}"
        )
    if not response.ok:
        raise EspnFetchError(f"ESPN returned HTTP {response.status_code}")

    league: dict[str, Any] = response.json()

    try:
        fa_response = session.get(
            url,
            params=[("view", "kona_player_info")],
            cookies=cookies,
            headers={"X-Fantasy-Filter": json.dumps(FREE_AGENT_FILTER)},
            timeout=30,
        )
        league["_freeAgents"] = fa_response.json().get("players", []) if fa_response.ok else []
    except requests.RequestException:
        league["_freeAgents"] = []

    return league
