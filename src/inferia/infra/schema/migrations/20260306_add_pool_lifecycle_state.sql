-- Migration: Add runtime lifecycle state for compute pools

DO $$ BEGIN
    CREATE TYPE pool_lifecycle_state AS ENUM ('running', 'terminating', 'terminated');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

ALTER TABLE compute_pools
ADD COLUMN IF NOT EXISTS lifecycle_state pool_lifecycle_state NOT NULL DEFAULT 'running';

CREATE INDEX IF NOT EXISTS idx_compute_pools_lifecycle_state
ON compute_pools (lifecycle_state);
