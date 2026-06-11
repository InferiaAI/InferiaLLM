-- Migration: Add missing indexes on hot-path columns (#81, #93, #94)

-- #81: policies table — queried on every inference request (cache miss)
CREATE INDEX IF NOT EXISTS idx_policies_org_id
    ON public.policies USING btree (org_id);

CREATE INDEX IF NOT EXISTS idx_policies_deployment_id
    ON public.policies USING btree (deployment_id);

-- #81: api_keys table — prefix queried on every API key auth
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix
    ON public.api_keys USING btree (prefix);

-- #81: audit_logs — timestamp range queries for filtering/archival
CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp
    ON public.audit_logs USING btree (timestamp DESC);

-- #93: model_deployments.node_ids — array containment check in placement queries
CREATE INDEX IF NOT EXISTS idx_model_deployments_node_ids_gin
    ON public.model_deployments USING gin (node_ids);

-- #94: compute_inventory (state, last_heartbeat) — liveness workers scan every 10s
CREATE INDEX IF NOT EXISTS idx_compute_inventory_state_heartbeat
    ON public.compute_inventory USING btree (state, last_heartbeat);

-- #82: billing_events.occurred_at — for retention/archival queries
CREATE INDEX IF NOT EXISTS idx_billing_events_occurred_at
    ON public.billing_events USING btree (occurred_at);

-- #82: outbox_events.status — for the publisher's pending scan
CREATE INDEX IF NOT EXISTS idx_outbox_events_status_created
    ON public.outbox_events USING btree (status, created_at)
    WHERE status = 'PENDING';
