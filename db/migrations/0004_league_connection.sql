-- Auth Phase B: connects a signed-in user to their real ESPN league.
-- Extends app_user (Phase A) rather than a separate join table -- this
-- phase is fixed at one league per user (see
-- docs/superpowers/specs/2026-08-14-auth-phase-b-league-connection-design.md,
-- Decision 2), so a 1:1 set of nullable columns is simpler than a table
-- whose only purpose would be a 1:1 relation. All six columns are NULL
-- together for a freshly signed-up user; espn_team_id specifically stays
-- NULL between "league connected" and "team picked" -- the two-step part
-- of the connect flow.
ALTER TABLE app_user
    ADD COLUMN espn_league_id      BIGINT,
    ADD COLUMN espn_season_id      INTEGER,
    ADD COLUMN espn_team_id        INTEGER,
    -- Fernet ciphertext (opaque bytes -- sim.api.crypto). NULL together for
    -- a public league, which needs no cookies at all.
    ADD COLUMN espn_s2_encrypted   BYTEA,
    ADD COLUMN espn_swid_encrypted BYTEA,
    ADD COLUMN league_connected_at TIMESTAMPTZ;
