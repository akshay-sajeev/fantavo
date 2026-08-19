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
from ingest.errors import IngestError
from ingest.espn_client import EspnFetchError, fetch_live_league
from sim.api.crypto import CredentialEncryptionError, decrypt_credential
from sim.api.league_connection_view import resolve_current_season_id

logger = logging.getLogger(__name__)

# Errors that mean "this one user's re-ingest legitimately can't succeed
# this cycle" rather than a bug in this module -- mirrors
# sim.api.precompute._SKIPPABLE_ERRORS. Isolated per-user so one bad
# credential or one malformed payload doesn't abort the whole batch.
_SKIPPABLE_ERRORS = (EspnFetchError, CredentialEncryptionError, IngestError)


def reingest_user(conn: psycopg.Connection[Any], user_id: int, now: datetime) -> None:
    """Re-fetches and re-ingests one user's connected league. Raises on
    failure -- reingest_all_connected_users catches _SKIPPABLE_ERRORS per
    user, this function itself does not swallow anything.

    The season is re-resolved fresh on every call (rather than re-using the
    value stored at connect time), so an already-connected user rolls over
    to the new NFL season instead of re-fetching a stale one forever; the
    stored column is then updated to whatever ingest_league actually parsed
    out of the real response, not the guessed value.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT espn_league_id, espn_s2_encrypted, espn_swid_encrypted
            FROM app_user WHERE user_id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"app_user row missing for user_id={user_id}")
    league_id, encrypted_s2, encrypted_swid = row

    season_id = resolve_current_season_id(now)
    espn_s2 = decrypt_credential(encrypted_s2) if encrypted_s2 else None
    swid = decrypt_credential(encrypted_swid) if encrypted_swid else None

    raw = fetch_live_league(league_id, season_id, espn_s2, swid)
    summary = ingest_league(conn, raw, ingested_at=now)

    with conn.transaction():
        conn.execute(
            "UPDATE app_user SET espn_season_id = %s WHERE user_id = %s",
            (summary.season_id, user_id),
        )


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
        except _SKIPPABLE_ERRORS as exc:
            conn.rollback()
            logger.warning("skipped re-ingest for user_id=%s: %s", user_id, exc)
