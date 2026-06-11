-- Node-centric refactor.
--
-- Operators interact with Nodes (compute_inventory rows), not Pools. Each
-- node carries a free-form labels jsonb map (K8s-style); pools stay in the
-- schema for FK invariance only and become "one __default__ row per org"
-- that operators never see.

-- 1. labels column on compute_inventory.
ALTER TABLE compute_inventory
ADD COLUMN IF NOT EXISTS labels jsonb NOT NULL DEFAULT '{}'::jsonb;

-- 2. GIN index for label-selector queries. CONCURRENTLY can't run inside a
--    transaction block; psql split-file migrators tolerate this fine when
--    each statement is sent independently. If your migrator wraps the file
--    in a transaction, swap to a plain `CREATE INDEX` — it still works,
--    just blocks writes for the duration.
CREATE INDEX IF NOT EXISTS idx_compute_inventory_labels
    ON compute_inventory USING GIN (labels);

-- 3. Default pool per organization. Operators never see this row; the
--    new /v1/nodes/add/* flow inserts every node into it so the existing
--    FK compute_inventory.pool_id -> compute_pools.id stays satisfiable.
INSERT INTO compute_pools (
    id, pool_name, owner_type, owner_id, provider, pool_type,
    allowed_gpu_types, max_cost_per_hour, scheduling_policy,
    provider_pool_id, is_active
)
SELECT
    gen_random_uuid(),
    '__default__',
    'organization',
    o.id::text,
    'on_prem',
    'job',
    ARRAY['any']::text[],
    0,
    '{}'::jsonb,
    'default:' || o.id::text,
    true
FROM organizations o
WHERE NOT EXISTS (
    SELECT 1
    FROM compute_pools p
    WHERE p.owner_id = o.id::text
      AND p.pool_name = '__default__'
);
