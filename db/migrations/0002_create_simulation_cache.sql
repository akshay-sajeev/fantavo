-- Cached simulate_seasons() output per (league_id, season_id), following the
-- same "JSONB alongside normalized tables" convention Phase 3 already used
-- for raw ESPN payloads (CLAUDE.md's storage convention). This is what
-- GET /league/{id}/simulation serves -- the scheduled precompute job
-- (sim/api/precompute.py) is the only thing that writes it.
--
-- One row per (league_id, season_id): a fresh precompute run replaces the
-- previous cached result in place (upsert), it does not accumulate history.
-- `computed_at` and `seed` are both stored explicitly, mirroring
-- `league.ingested_at`'s "never a server-side now() default" discipline
-- (see ingest/db.py) -- the seed in particular is what makes "curl returns
-- the same title odds as a direct engine call with the same seed"
-- (Phase 4's done criterion) a checkable claim: the exact seed used is
-- always readable back out of this row, never a hidden implementation
-- detail.

CREATE TABLE IF NOT EXISTS simulation_cache (
    league_id    BIGINT NOT NULL,
    season_id    INTEGER NOT NULL,
    n_sims       INTEGER NOT NULL,
    -- The np.random.default_rng() seed simulate_seasons() was called with
    -- for this cached result. See sim/api/seeds.py::precompute_seed for how
    -- it is derived (deterministic, from league_id + season_id).
    seed         BIGINT NOT NULL,
    computed_at  TIMESTAMPTZ NOT NULL,
    -- SimulationResult, JSON-shaped by sim/api/cache.py::serialize_result.
    -- Keeps full per-team distributions (finish_distribution included), not
    -- just a bare title-odds scalar -- CLAUDE.md's "distributions, not
    -- point estimates" rule applies to this cached response shape too.
    result       JSONB NOT NULL,
    PRIMARY KEY (league_id, season_id),
    FOREIGN KEY (league_id, season_id)
        REFERENCES league (league_id, season_id) ON DELETE CASCADE
);
