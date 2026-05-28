-- Migration 20260528_provisioning_jobs.sql (idempotent)
-- Postgres ≥ 12 allows ALTER TYPE ADD VALUE inside a transaction, but the
-- new value cannot be USED in the same transaction. The split-file migrator
-- runs each .sql file in autocommit, so each statement below commits independently.

-- 1. Extend node_state enum with 'failed' (does NOT overload 'unhealthy',
--    which already means "registered worker stopped heartbeating").
ALTER TYPE node_state ADD VALUE IF NOT EXISTS 'failed';

-- 2. Extend compute_inventory with class/type columns.
ALTER TABLE compute_inventory
    ADD COLUMN IF NOT EXISTS instance_class TEXT
        CHECK (instance_class IN ('normal_gpu','heavy_gpu','cpu')),
    ADD COLUMN IF NOT EXISTS instance_type  TEXT;

-- 3. Create the provisioning_jobs queue table.
CREATE TABLE IF NOT EXISTS provisioning_jobs (
    id                   UUID PRIMARY KEY,
    node_id              UUID NOT NULL REFERENCES compute_inventory(id) ON DELETE CASCADE,
    pool_id              UUID NOT NULL,
    org_id               TEXT NOT NULL,
    provider             TEXT NOT NULL,
    spec                 JSONB NOT NULL,

    phase                TEXT NOT NULL,
    attempt_count        INT  NOT NULL DEFAULT 0,
    next_attempt_after   TIMESTAMPTZ,

    last_error_code      TEXT,
    last_error_message   TEXT,
    last_error_hint      TEXT,
    error_class          TEXT,

    lease_holder         TEXT,
    lease_expires_at     TIMESTAMPTZ,

    pulumi_stack_outputs JSONB,

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT provisioning_jobs_phase_check
        CHECK (phase IN ('pending','preflight','provisioning','bootstrapping',
                         'ready','failed','cancelling','terminated'))
);

CREATE INDEX IF NOT EXISTS provisioning_jobs_claimable_idx
    ON provisioning_jobs (next_attempt_after NULLS FIRST, updated_at)
    WHERE phase IN ('pending','preflight','provisioning','bootstrapping','cancelling');

CREATE INDEX IF NOT EXISTS provisioning_jobs_node_id_idx
    ON provisioning_jobs (node_id);

-- 4. One-time backfill: any in-flight compute_inventory rows under the old
--    fire-and-forget adapter get a 'failed/UPGRADE_ABANDONED' job + the
--    inventory row transitions to 'failed'.
INSERT INTO provisioning_jobs (
    id, node_id, pool_id, org_id, provider, spec,
    phase, last_error_code, last_error_message, last_error_hint,
    error_class, attempt_count, created_at, updated_at
)
SELECT gen_random_uuid(), ci.id, ci.pool_id,
       COALESCE(cp.org_id, 'unknown'),
       ci.provider::text, '{}'::jsonb,
       'failed', 'UPGRADE_ABANDONED',
       'This node was provisioned by an older version of inferia-app. '
       || 'State was lost on upgrade. Delete the node and create it again '
       || 'from the wizard.',
       'Delete and recreate from the wizard.',
       'PERMANENT', 0, now(), now()
FROM compute_inventory ci
LEFT JOIN compute_pools cp ON cp.id = ci.pool_id
WHERE ci.state = 'provisioning'
  AND ci.agent_kind = 'worker'
  AND NOT EXISTS (
      SELECT 1 FROM provisioning_jobs pj WHERE pj.node_id = ci.id
  );

UPDATE compute_inventory SET state = 'failed', updated_at = now()
WHERE state = 'provisioning'
  AND agent_kind = 'worker'
  AND id IN (SELECT node_id FROM provisioning_jobs WHERE last_error_code = 'UPGRADE_ABANDONED');
