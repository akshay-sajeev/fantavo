"""The one precompute function both the scheduled job
(`sim/api/scheduler.py`) and any manual/local run (e.g. verification
scripts) call.

CLAUDE.md's "one simulation engine" rule applies here too: this module does
not compute anything on its own -- it only wires
`sim.api.params_loader.load_league` -> `sim.engine.simulate_seasons` ->
`sim.api.cache.write_cached_simulation` together with an explicit,
deterministic seed (`sim.api.seeds.precompute_seed`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np
import psycopg

from ingest.errors import IngestError
from sim.api.cache import write_cached_simulation
from sim.api.params_loader import LeagueNotIngestedError, load_league
from sim.api.seeds import precompute_seed
from sim.engine import simulate_seasons
from sim.params.errors import ParamsError

logger = logging.getLogger(__name__)

# Larger than a live what-if's 2,000: this runs on a schedule, not in
# response to a waiting user, so it can afford more sims for a tighter
# confidence interval on the numbers everyone reads from the cache. Matches
# the sim count sim/params/validate.py already uses for its own sanity-check
# runs (VALIDATION_SIMS), not a new arbitrary figure.
PRECOMPUTE_N_SIMS = 10_000

# Errors that mean "this league legitimately can't be simulated yet" (no
# drafted roster, no fitted variance data, etc.) rather than a bug in this
# module. Precompute skips and logs these per-league instead of crashing the
# whole job over one not-yet-ready league.
_SKIPPABLE_ERRORS = (LeagueNotIngestedError, IngestError, ParamsError)


def precompute_league(
    conn: psycopg.Connection[Any],
    league_id: int,
    season_id: int,
    computed_at: datetime,
    n_sims: int = PRECOMPUTE_N_SIMS,
) -> int:
    """Simulate and cache one league/season. Returns the seed used."""
    loaded = load_league(conn, league_id, season_id)
    seed = precompute_seed(league_id, season_id)
    rng = np.random.default_rng(seed)
    result = simulate_seasons(loaded.league, n_sims=n_sims, rng=rng)
    write_cached_simulation(conn, league_id, season_id, result, seed, computed_at)
    return seed


def precompute_all_leagues(
    conn: psycopg.Connection[Any], computed_at: datetime | None = None
) -> None:
    """Precompute every currently-ingested (league_id, season_id).

    Leagues with no drafted roster yet (`RosterNotAvailableError`, a subclass
    of `IngestError`) are skipped with a log line, not silently ignored and
    not fabricated around -- e.g. this was `fixtures/league_raw_2026.json`'s
    real league for Phases 1-5 while it was still pre-draft (see
    docs/decisions.md); it is real and drafted as of the "no more mock data"
    update and precomputes normally now, but any other not-yet-drafted
    league ingested in the future still hits this same skip path.
    """
    resolved_computed_at = computed_at or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("SELECT league_id, season_id FROM league")
        pairs = cur.fetchall()

    for league_id, season_id in pairs:
        try:
            seed = precompute_league(conn, league_id, season_id, resolved_computed_at)
            conn.commit()
            logger.info(
                "precomputed league_id=%s season_id=%s seed=%s", league_id, season_id, seed
            )
        except _SKIPPABLE_ERRORS as exc:
            conn.rollback()
            logger.warning(
                "skipped precompute for league_id=%s season_id=%s: %s", league_id, season_id, exc
            )


def _main() -> None:
    """Run the precompute job once, immediately, against a real DSN --
    useful for local verification and for populating the cache right after
    an ingest, without waiting for the scheduler's next interval.
    """
    import argparse

    from ingest.db import DEFAULT_DEV_DSN, connect, dsn_from_env

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=None, help=f"Postgres DSN (default: {DEFAULT_DEV_DSN})")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    dsn: str = args.dsn or dsn_from_env("DATABASE_URL", DEFAULT_DEV_DSN)
    with connect(dsn) as conn:
        precompute_all_leagues(conn)
    print(f"Precompute run complete against {dsn}")


if __name__ == "__main__":
    _main()
