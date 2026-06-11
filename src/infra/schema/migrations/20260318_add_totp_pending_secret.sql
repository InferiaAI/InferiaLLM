-- Add totp_pending_secret column to users table
-- This column stores the TOTP secret during setup before the user confirms it
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_pending_secret VARCHAR;
