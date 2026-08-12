-- Normalized tables for one ingested ESPN fantasy league/season, each paired
-- with a raw JSONB column holding the corresponding slice of ESPN's payload,
-- so historical data can be reprocessed without re-fetching (CLAUDE.md
-- convention).
--
-- Grain is (league_id, season_id, ...) throughout: an ESPN league_id is
-- stable across seasons, but scoring settings, teams, rosters, matchups and
-- the free-agent player pool are all season-specific -- a league can change
-- its scoring rules or roster composition year to year, and a re-fetch of a
-- past season must not collide with the current one.
--
-- `ingested_at` is populated by the application (ingest/db.py), not a
-- server-side DEFAULT now() -- see that module's docstring for why an
-- explicit, caller-supplied timestamp is required for idempotency to be
-- testable at all.

CREATE TABLE IF NOT EXISTS league (
    league_id             BIGINT NOT NULL,
    season_id             INTEGER NOT NULL,
    name                  TEXT NOT NULL,
    size                  INTEGER NOT NULL,
    teams_joined          INTEGER NOT NULL,
    is_full               BOOLEAN NOT NULL,
    is_drafted            BOOLEAN NOT NULL,
    n_regular_weeks       INTEGER NOT NULL,
    n_playoff_teams       INTEGER NOT NULL,
    points_per_reception  DOUBLE PRECISION NOT NULL,
    -- Full raw ESPN fixture payload for this league/season (already scrubbed
    -- of espn_s2/SWID upstream by scripts/fetch_fixture.py -- see
    -- docs/decisions.md Phase 0). Lets the whole normalization pipeline be
    -- rerun from scratch without re-fetching.
    raw_payload           JSONB NOT NULL,
    ingested_at           TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (league_id, season_id)
);

CREATE TABLE IF NOT EXISTS scoring_settings (
    league_id             BIGINT NOT NULL,
    season_id             INTEGER NOT NULL,
    points_per_reception  DOUBLE PRECISION NOT NULL,
    -- settings.scoringSettings verbatim -- what ingest.scoring.build_scoring_table
    -- consumes. Stored separately from league.raw_payload so the scoring
    -- table alone can be rebuilt without touching the whole fixture.
    raw_scoring_settings  JSONB NOT NULL,
    ingested_at           TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (league_id, season_id),
    FOREIGN KEY (league_id, season_id)
        REFERENCES league (league_id, season_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS team (
    league_id    BIGINT NOT NULL,
    season_id    INTEGER NOT NULL,
    team_id      INTEGER NOT NULL,
    name         TEXT NOT NULL,
    abbrev       TEXT NOT NULL,
    -- Whether this team slot is claimed by a manager. This league is
    -- configured for 12 teams with only 10 joined (see docs/decisions.md
    -- Phase 1) -- unclaimed slots are still real schedule opponents and must
    -- round-trip through this table, not be dropped.
    has_owner    BOOLEAN NOT NULL,
    raw_team     JSONB NOT NULL,
    ingested_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (league_id, season_id, team_id),
    FOREIGN KEY (league_id, season_id)
        REFERENCES league (league_id, season_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS player (
    league_id             BIGINT NOT NULL,
    season_id             INTEGER NOT NULL,
    player_id             BIGINT NOT NULL,
    name                  TEXT NOT NULL,
    default_position_id   INTEGER NOT NULL,
    -- Flex eligibility must be read from eligibleSlots, never inferred from
    -- defaultPositionId (CLAUDE.md domain glossary) -- stored as the full
    -- set, not collapsed to one position.
    eligible_slots        INTEGER[] NOT NULL,
    season_total           DOUBLE PRECISION NOT NULL,
    games_projected        INTEGER NOT NULL,
    mean_points_per_game   DOUBLE PRECISION NOT NULL,
    percent_owned          DOUBLE PRECISION NOT NULL,
    percent_started         DOUBLE PRECISION NOT NULL,
    raw_player              JSONB NOT NULL,
    ingested_at              TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (league_id, season_id, player_id),
    FOREIGN KEY (league_id, season_id)
        REFERENCES league (league_id, season_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS roster (
    league_id       BIGINT NOT NULL,
    season_id       INTEGER NOT NULL,
    team_id         INTEGER NOT NULL,
    player_id       BIGINT NOT NULL,
    -- Every roster entry is stored, not just starters (lineupSlotId 20/21
    -- for bench/IR included) -- filtering to a starting lineup is
    -- ingest.parse.build_team_params's job when constructing sim.engine
    -- inputs, not persistence's.
    lineup_slot_id  INTEGER NOT NULL,
    raw_entry       JSONB NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (league_id, season_id, team_id, player_id),
    FOREIGN KEY (league_id, season_id, team_id)
        REFERENCES team (league_id, season_id, team_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS matchup (
    league_id           BIGINT NOT NULL,
    season_id           INTEGER NOT NULL,
    -- ESPN's own schedule[].id -- unique within a league/season and stable
    -- across re-fetches, used as the natural key instead of a synthetic one.
    espn_matchup_id     INTEGER NOT NULL,
    -- Deliberately matchupPeriodId, not scoringPeriodId -- CLAUDE.md is
    -- explicit these are different fields (a fantasy matchup vs an NFL
    -- week) and not always equal.
    matchup_period_id   INTEGER NOT NULL,
    -- Nullable: a bye week (one side of the matchup absent) is valid raw
    -- data even though this fixture's schedule happens not to contain one.
    home_team_id        INTEGER,
    away_team_id         INTEGER,
    winner                TEXT,
    -- Note (carried over from docs/decisions.md Phase 1): the raw
    -- home/away.rosterForMatchupPeriod.entries blocks inside this JSON blob
    -- can look like a populated roster even pre-draft -- that's an ESPN
    -- preview/placeholder artifact, not authoritative roster data. Stored
    -- here verbatim for reprocessing, but never read as a roster source;
    -- the `roster` table is the authoritative one.
    raw_matchup           JSONB NOT NULL,
    ingested_at            TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (league_id, season_id, espn_matchup_id),
    FOREIGN KEY (league_id, season_id)
        REFERENCES league (league_id, season_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_matchup_period
    ON matchup (league_id, season_id, matchup_period_id);
