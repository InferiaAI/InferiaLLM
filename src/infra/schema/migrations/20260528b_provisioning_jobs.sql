-- Migration 20260528b_provisioning_jobs.sql (idempotent)
--
-- WHY THIS IS A SEPARATE FILE:
-- cli_init.py wraps every migration file in a single BEGIN/COMMIT transaction
-- (see src/cli_init.py:99-106). The previous file
-- (20260528a_node_state_failed.sql) committed the 'failed' addition to the
-- node_state enum. Postgres >= 12 forbids referencing a newly-added enum
-- value in the same transaction that created it (SQLSTATE 55P04), so the
-- UPDATE compute_inventory SET state = 'failed' below MUST live in a
-- separate file that runs after 20260528a has committed.
--
-- WARNING: tests/integration/test_migration.py splits this file on ';'.
-- Do not introduce $$-quoted blocks here without updating the test
-- to use a real statement splitter.

-- 1. Extend compute_inventory with class/type columns.
ALTER TABLE compute_inventory
    ADD COLUMN IF NOT EXISTS instance_class TEXT
        CHECK (instance_class IN ('normal_gpu','heavy_gpu','cpu')),
    ADD COLUMN IF NOT EXISTS instance_type  TEXT;

-- 2. Create the provisioning_jobs queue table.
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
                         'ready','failed','cancelling','terminated')),
    CONSTRAINT provisioning_jobs_error_class_check
        CHECK (error_class IS NULL OR error_class IN ('TRANSIENT','PERMANENT','INFRASTRUCTURE'))
);

CREATE INDEX IF NOT EXISTS provisioning_jobs_claimable_idx
    ON provisioning_jobs (next_attempt_after NULLS FIRST, updated_at)
    WHERE phase IN ('pending','preflight','provisioning','bootstrapping','cancelling');

CREATE INDEX IF NOT EXISTS provisioning_jobs_node_id_idx
    ON provisioning_jobs (node_id);

-- 3. One-time backfill: any in-flight compute_inventory rows under the old
--    fire-and-forget adapter get a 'failed/UPGRADE_ABANDONED' job + the
--    inventory row transitions to 'failed'. The 'failed' enum value was
--    committed by 20260528a_node_state_failed.sql, so it is safe to use
--    here.
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

-- 4. Column-intent documentation. These COMMENT statements record design
--    decisions that are otherwise invisible at the schema level.
COMMENT ON COLUMN provisioning_jobs.spec IS
    'JSONB payload from POST /api/v1/nodes/add/{provider}; NOT NULL with no default -- every enqueue path must provide it explicitly (use ''{}''::jsonb if truly empty).';

COMMENT ON COLUMN provisioning_jobs.provider IS
    'Provider identifier. Today only ''aws'' is exercised by the reconciler; deliberately unconstrained TEXT for forward compatibility with future providers.';
