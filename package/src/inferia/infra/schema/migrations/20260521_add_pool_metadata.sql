-- Add metadata jsonb column to compute_pools for provider-specific
-- configuration (e.g. AWS subnet_id, security_group_ids).
-- Also add org_id as an alias for owner_id to support direct queries
-- from the AWS adapter (which selects org_id from compute_pools).

ALTER TABLE compute_pools
    ADD COLUMN IF NOT EXISTS metadata jsonb;

-- org_id: generated column that aliases owner_id for backward-compat
-- with AWS adapter code that queries SELECT org_id FROM compute_pools.
-- Stored as a plain nullable column; backfilled from owner_id.
ALTER TABLE compute_pools
    ADD COLUMN IF NOT EXISTS org_id text;

UPDATE compute_pools
SET org_id = owner_id
WHERE org_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_compute_pools_org_id
    ON compute_pools (org_id);
