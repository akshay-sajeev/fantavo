-- Auth: accounts and sessions. "user" is a reserved word in Postgres, hence
-- app_user. Every timestamp here is application-supplied (see
-- ingest/db.py's ingested_at convention) -- never a server-side DEFAULT
-- now(), so tests can exercise expiry/throttle windows deterministically.

CREATE TABLE IF NOT EXISTS app_user (
    user_id       BIGSERIAL PRIMARY KEY,
    -- As the user typed it, for display.
    email         TEXT NOT NULL,
    -- Lowercased/trimmed -- the actual lookup key. A separate column
    -- (rather than a functional unique index) so the normalization rule
    -- stays visible in the schema and lives in exactly one Python function
    -- (auth_view.normalize_email).
    email_norm    TEXT NOT NULL UNIQUE,
    -- argon2id output (auth_view.hash_password). Never the password in any
    -- form.
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS user_session (
    -- sha256 of the opaque session token, NOT the token itself -- a
    -- database leak must not hand over live sessions, the same reasoning
    -- that makes password_hash a hash rather than plaintext.
    token_hash   TEXT PRIMARY KEY,
    user_id      BIGINT NOT NULL
        REFERENCES app_user (user_id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_session_user
    ON user_session (user_id);

-- Keyed by email_norm and deliberately NOT foreign-keyed to app_user:
-- failed attempts against an email with no account must be throttled
-- identically to one that has an account, or the lockout behavior itself
-- becomes a way to learn which emails are registered.
CREATE TABLE IF NOT EXISTS login_throttle (
    email_norm      TEXT PRIMARY KEY,
    failed_count    INTEGER NOT NULL,
    first_failed_at TIMESTAMPTZ NOT NULL,
    locked_until    TIMESTAMPTZ
);
