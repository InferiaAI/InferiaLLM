-- Stamped into every access and refresh token as "tv". Logout and a password
-- change bump it, which ends every session issued before.
ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;
