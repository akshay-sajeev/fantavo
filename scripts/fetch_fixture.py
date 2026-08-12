#!/usr/bin/env python3
"""Fetch a league snapshot from ESPN and save it to /fixtures, scrubbed.

This is the ONLY script that touches the ESPN API. Everything else -- parsing,
analytics, tests, iteration -- runs against the saved fixtures. ESPN's fantasy API
is undocumented, unofficial and rate-limited; hitting it in a dev loop will get you
throttled and will make failures look like code bugs when they aren't.

Usage:
    cp .env.example .env      # then fill in LEAGUE_ID, ESPN_S2, SWID
    python scripts/fetch_fixture.py
    python scripts/fetch_fixture.py --season 2025 --name last_season

Public leagues need no cookies. Private leagues need ESPN_S2 and SWID, which are
session cookies for your ESPN account -- treat them like passwords. This script
never prints them and strips them from anything it writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import requests

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

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

# Keys whose values identify a real person. Fixtures get committed to the repo, so
# these are pseudonymized rather than kept.
PII_KEYS = {"firstName", "lastName", "displayName", "email", "notificationSettings"}
# Keys that could carry credentials. These are dropped outright.
SECRET_KEYS = {"espn_s2", "espnS2", "SWID", "swid", "cookie", "Cookie", "authorization"}

# ESPN references league members by SWID-shaped GUID. These appear as dict values
# (members[].id) but ALSO as bare strings inside lists (teams[].owners) and as
# plain fields (teams[].primaryOwner). Your own SWID is one of them, so any
# scrubber that only inspects dict keys will leak your live session token.
_GUID_RE = re.compile(
    r"^\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?$"
)


def _pseudonym(value: str, prefix: str) -> str:
    """Stable fake value, so the same person maps consistently across a fixture."""
    digest = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"{prefix}_{digest}"


def scrub(node: Any) -> Any:
    """Recursively remove credentials and pseudonymize personal data.

    GUIDs are pseudonymized deterministically, so teams[].owners still resolves
    to the matching members[].id in the saved fixture.
    """
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in SECRET_KEYS:
                continue
            if key in PII_KEYS:
                out[key] = _pseudonym(str(value), key) if isinstance(value, str) else None
            else:
                out[key] = scrub(value)
        return out
    if isinstance(node, list):
        return [scrub(item) for item in node]
    if isinstance(node, str) and _GUID_RE.match(node):
        return _pseudonym(node, "member")
    return node


def find_leaks(node: Any, secrets: list[str], path: str = "$") -> list[str]:
    """Locate surviving credentials by JSON path, without echoing their values."""
    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            hits += find_leaks(value, secrets, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            hits += find_leaks(value, secrets, f"{path}[{i}]")
    elif isinstance(node, str):
        hits += [path for s in secrets if s and s in node]
    return hits


def assert_clean(payload: Any, secrets: list[str]) -> None:
    """Last line of defence: fail loudly rather than commit a live session token."""
    leaks = find_leaks(payload, secrets)
    if leaks:
        shown = "\n  ".join(sorted(set(leaks))[:10])
        raise SystemExit(
            "ABORT: a credential survived scrubbing and would have been written to "
            f"disk. Nothing was saved.\n\nFound at:\n  {shown}\n\n"
            "Add the offending key to SECRET_KEYS or PII_KEYS in this script, or "
            "extend scrub() to cover that shape. Do not work around this by "
            "disabling the check."
        )


def load_env() -> dict[str, str]:
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

    league_id = os.environ.get("LEAGUE_ID")
    if not league_id:
        raise SystemExit("LEAGUE_ID is not set. Copy .env.example to .env and fill it in.")
    return {
        "league_id": league_id,
        "espn_s2": os.environ.get("ESPN_S2", ""),
        "swid": os.environ.get("SWID", ""),
    }


def fetch(season: int, league_id: str, cookies: dict[str, str]) -> dict[str, Any]:
    url = f"{BASE}/seasons/{season}/segments/0/leagues/{league_id}"
    params = [("view", v) for v in VIEWS]

    response = requests.get(url, params=params, cookies=cookies, timeout=30)
    if response.status_code == 401:
        raise SystemExit(
            "401 from ESPN. For a private league you need valid ESPN_S2 and SWID "
            "cookies; they expire, so re-copy them from your browser."
        )
    response.raise_for_status()
    league = response.json()

    # Free agents come from the same endpoint but need the X-Fantasy-Filter header.
    fa = requests.get(
        url,
        params=[("view", "kona_player_info")],
        cookies=cookies,
        headers={"X-Fantasy-Filter": json.dumps(FREE_AGENT_FILTER)},
        timeout=30,
    )
    if fa.ok:
        league["_freeAgents"] = fa.json().get("players", [])
    else:
        print(f"  warning: free agent fetch returned {fa.status_code}, skipping")

    return league


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--name", default="league_raw", help="fixture filename stem")
    args = parser.parse_args()

    env = load_env()
    cookies = {}
    if env["espn_s2"] and env["swid"]:
        cookies = {"espn_s2": env["espn_s2"], "SWID": env["swid"]}
        print("Using cookies for private league access.")
    else:
        print("No cookies found; assuming a public league.")

    print(f"Fetching league {env['league_id']}, season {args.season}...")
    raw = fetch(args.season, env["league_id"], cookies)

    cleaned = scrub(raw)
    assert_clean(cleaned, [env["espn_s2"], env["swid"]])

    FIXTURES.mkdir(exist_ok=True)
    out = FIXTURES / f"{args.name}_{args.season}.json"
    out.write_text(json.dumps(cleaned, indent=2, sort_keys=True))

    size_kb = out.stat().st_size / 1024
    teams = len(cleaned.get("teams", []))
    print(f"Wrote {out.relative_to(FIXTURES.parent)} ({size_kb:.0f} KB, {teams} teams)")
    print("Develop against this file. Do not call ESPN again until the schema changes.")


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as exc:
        # Print the exception type only -- the full exception can echo the request,
        # cookies included.
        print(f"Network error: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None