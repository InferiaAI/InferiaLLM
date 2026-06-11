-- 20260520_add_worker_bootstrap_tokens.sql
-- One-shot tokens minted by the orchestration service when provisioning a
-- new node (initially AWS EC2, eventually any cloud adapter). The token is
-- embedded in cloud-init user-data; the worker presents it once to
-- /v1/workers/register, the row is atomically marked consumed, and the
-- worker receives a long-lived WorkerJWT in exchange.
--
-- Note: org_id is text (not uuid) to match organizations.id VARCHAR.
-- Note: pool_id is uuid to match compute_pools.id uuid.

CREATE TABLE IF NOT EXISTS worker_bootstrap_tokens (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash        text NOT NULL UNIQUE,
    pool_id           uuid NOT NULL REFERENCES compute_pools(id) ON DELETE CASCADE,
    org_id            text NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    expires_at        timestamptz NOT NULL,
    consumed_at       timestamptz NULL,
    consumed_node_id  uuid NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_worker_bootstrap_tokens_pool
    ON worker_bootstrap_tokens(pool_id);

CREATE INDEX IF NOT EXISTS idx_worker_bootstrap_tokens_unconsumed
    ON worker_bootstrap_tokens(expires_at)
    WHERE consumed_at IS NULL;
