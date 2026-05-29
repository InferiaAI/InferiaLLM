-- package/src/inferia/infra/schema/migrations/20260530_pool_first_deployments.sql
--
-- Pool-first deployments: deployments learn target_pool_id / target_node_id,
-- compute_pools learns max_nodes, and we add a partial index for the
-- DeploymentLinker hot path. CREATE INDEX CONCURRENTLY cannot run inside
-- a transaction, so the migration runner splits on the @SPLIT@ marker
-- and runs each chunk separately.

ALTER TABLE model_deployments
  ADD COLUMN IF NOT EXISTS target_pool_id uuid REFERENCES compute_pools(id),
  ADD COLUMN IF NOT EXISTS target_node_id uuid REFERENCES compute_inventory(id);

UPDATE model_deployments
   SET target_pool_id = pool_id
 WHERE target_pool_id IS NULL
   AND pool_id IS NOT NULL;

ALTER TABLE compute_pools
  ADD COLUMN IF NOT EXISTS max_nodes int;

-- @SPLIT@

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_model_deployments_pending_node
  ON model_deployments (target_pool_id, state)
  WHERE state = 'PENDING_NODE';
