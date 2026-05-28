-- 20260525_add_node_provisioning_events.sql
-- Append-only event log for provider provisioning UX. One row per phase
-- state transition and per Pulumi/cloud-init log line. Read with the
-- cursor `WHERE pool_id=$1 AND id > $2 ORDER BY id LIMIT $3` for the
-- dashboard polling path.

CREATE TABLE IF NOT EXISTS node_provisioning_events (
    id         BIGSERIAL PRIMARY KEY,
    pool_id    UUID        NOT NULL,
    node_id    UUID,
    phase      TEXT        NOT NULL,
    status     TEXT        NOT NULL,
    message    TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_node_provisioning_events_pool_id_id
    ON node_provisioning_events (pool_id, id);
