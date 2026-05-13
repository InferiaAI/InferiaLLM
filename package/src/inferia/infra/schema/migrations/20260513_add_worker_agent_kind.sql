-- Migration: introduce inferia-worker as a managed-node kind
--
-- Direct-managed GPU hosts running the inferia-worker agent (bare-metal /
-- self-hosted server / operator-provisioned cloud VM) are distinguished from
-- DePIN-provider nodes (akash, nosana) by a new agent_kind column, alongside
-- the existing 'provider' enum. The control plane routes load/unload to the
-- right backend based on this value.
--
-- Forward-only: existing rows get agent_kind='unknown' (their behaviour is
-- unchanged because the worker_controller only acts on rows with kind='worker').

DO $$ BEGIN
    CREATE TYPE node_agent_kind AS ENUM ('unknown', 'worker', 'akash', 'nosana', 'llmd');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

ALTER TABLE compute_inventory
ADD COLUMN IF NOT EXISTS agent_kind node_agent_kind NOT NULL DEFAULT 'unknown';

-- node_name is required by the worker protocol but the existing schema has only
-- hostname (which may collide across pools). Add a separate column keyed
-- (pool_id, node_name) for the worker registration handshake.
ALTER TABLE compute_inventory
ADD COLUMN IF NOT EXISTS node_name text;

CREATE UNIQUE INDEX IF NOT EXISTS uq_compute_inventory_pool_node_name
    ON compute_inventory (pool_id, node_name)
    WHERE agent_kind = 'worker' AND node_name IS NOT NULL;

-- Partial index on (kind='worker') — most lookups from the WS layer scan only
-- worker-kind rows.
CREATE INDEX IF NOT EXISTS idx_compute_inventory_worker
    ON compute_inventory (agent_kind, state)
    WHERE agent_kind = 'worker';
