"""Scheduled job that precomputes and caches `simulate_seasons()` output for
every ingested league, on an interval, via APScheduler.

**Library choice: APScheduler's `BackgroundScheduler`, not Celery/RQ/cron.**
This is a single-process FastAPI service with no existing task queue or
worker infrastructure (see docs/decisions.md Phase 4). APScheduler runs an
in-process background thread alongside uvicorn with no extra moving parts --
no broker, no separate worker process, no new infra to run locally -- which
fits this project's current hobby/single-instance scale. Revisit if this
service ever runs as multiple replicas, where an in-process interval
scheduler would double-run (or N-times-run) the job; at that point a real
queue or a `pg_cron`-style DB-scheduled job would be the right fix.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from ingest.db import DEFAULT_DEV_DSN, connect, dsn_from_env
from sim.api.precompute import precompute_all_leagues
from sim.api.reingest import reingest_all_connected_users

logger = logging.getLogger(__name__)

# Every 6 hours: frequent enough that cached title odds don't go stale for
# days, infrequent enough not to burn CPU running a 10,000-sim Monte Carlo
# per league constantly. A documented, adjustable operational choice, not a
# fitted/modelling parameter.
PRECOMPUTE_INTERVAL_HOURS = 6

JOB_ID = "precompute_all_leagues"

# Same cadence as precompute -- no reason to invent a different one
# (Auth Phase B design doc). A user's connected league gets re-fetched from
# live ESPN this often, keeping scores/standings current through the
# season.
REINGEST_INTERVAL_HOURS = 6

REINGEST_JOB_ID = "reingest_all_connected_users"

# Re-ingest starts first; precompute is offset behind it by this much every
# cycle, so a precompute run never straddles a re-ingest commit for the same
# league under READ COMMITTED. Generous enough for a full re-ingest pass
# across all connected users at this project's hobby scale; revisit if that
# assumption stops holding.
PRECOMPUTE_OFFSET_MINUTES = 15

# APScheduler's IntervalTrigger only honors a `start_date` that is still in
# the future: a start_date already in the past is rounded *forward* by a
# whole interval (see IntervalTrigger.get_next_fire_time). Anchoring
# re-ingest at exactly `now` therefore pushed its first run out a full
# REINGEST_INTERVAL_HOURS and let precompute overtake it -- the opposite of
# the intended ordering. A small lead keeps the anchor in the future so both
# first runs happen shortly after startup, in the right order.
REINGEST_STARTUP_LEAD_SECONDS = 30


def _run_precompute_job(dsn: str) -> None:
    with connect(dsn) as conn:
        precompute_all_leagues(conn)


def _run_reingest_job(dsn: str) -> None:
    with connect(dsn) as conn:
        reingest_all_connected_users(conn)


def start_scheduler(dsn: str | None = None) -> BackgroundScheduler:
    """Start the background scheduler. Re-ingest runs shortly after startup
    (so a freshly started API isn't serving stale data for a full interval)
    and then every `REINGEST_INTERVAL_HOURS`; precompute follows
    `PRECOMPUTE_OFFSET_MINUTES` behind it and repeats every
    `PRECOMPUTE_INTERVAL_HOURS`. An `IntervalTrigger` repeats from its own
    `start_date`, so that offset holds on every future cycle -- not just the
    first -- keeping a precompute read from straddling a re-ingest commit and
    caching a simulation built from a mixed snapshot under READ COMMITTED.
    """
    resolved_dsn = dsn or dsn_from_env("DATABASE_URL", DEFAULT_DEV_DSN)
    reingest_start = datetime.now(timezone.utc) + timedelta(
        seconds=REINGEST_STARTUP_LEAD_SECONDS
    )
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _run_reingest_job,
        "interval",
        hours=REINGEST_INTERVAL_HOURS,
        start_date=reingest_start,
        args=[resolved_dsn],
        id=REINGEST_JOB_ID,
        replace_existing=True,
    )
    scheduler.add_job(
        _run_precompute_job,
        "interval",
        hours=PRECOMPUTE_INTERVAL_HOURS,
        start_date=reingest_start + timedelta(minutes=PRECOMPUTE_OFFSET_MINUTES),
        args=[resolved_dsn],
        id=JOB_ID,
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "started precompute scheduler: every %sh, dsn=%s", PRECOMPUTE_INTERVAL_HOURS, resolved_dsn
    )
    logger.info(
        "started reingest scheduler: every %sh, dsn=%s", REINGEST_INTERVAL_HOURS, resolved_dsn
    )
    return scheduler
