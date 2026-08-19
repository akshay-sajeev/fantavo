"""Recurring re-ingest of every user's connected ESPN league (Auth Phase B)
-- sim/api/scheduler.py's second scheduled job, alongside precompute_all_
leagues. Mirrors sim/api/precompute.py's per-user error isolation: one
user's fetch failure (revoked cookies, a transient ESPN error) is logged
and skipped, never allowed to abort the rest of the batch.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import psycopg

from ingest.db import ingest_league
from ingest.espn_client import EspnFetchError, fetch_live_league
from sim.api.crypto import decrypt_credential

logger = logging.getLogger(__name__)


def reingest_user(conn: psycopg.Connection[Any], user_id: int, now: datetime) -> None:
    """Re-fetches and re-ingests one user's connected league. Raises
    EspnFetchError on failure -- reingest_all_connected_users catches it
    per user, this function itself does not swallow anything."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT espn_league_id, espn_season_id, espn_s2_encrypted, espn_swid_encrypted
            FROM app_user WHERE user_id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()
    assert row is not None
    league_id, season_id, encrypted_s2, encrypted_swid = row

    espn_s2 = decrypt_credential(encrypted_s2) if encrypted_s2 else None
    swid = decrypt_credential(encrypted_swid) if encrypted_swid else None

    raw = fetch_live_league(league_id, season_id, espn_s2, swid)
    ingest_league(conn, raw, ingested_at=now)


def reingest_all_connected_users(
    conn: psycopg.Connection[Any], now: datetime | None = None
) -> None:
    resolved_now = now or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("SELECT user_id FROM app_user WHERE espn_league_id IS NOT NULL")
        user_ids = [row[0] for row in cur.fetchall()]

    for user_id in user_ids:
        try:
            reingest_user(conn, user_id, resolved_now)
            conn.commit()
            logger.info("re-ingested league for user_id=%s", user_id)
        except EspnFetchError as exc:
            conn.rollback()
            logger.warning("skipped re-ingest for user_id=%s: %s", user_id, exc)
