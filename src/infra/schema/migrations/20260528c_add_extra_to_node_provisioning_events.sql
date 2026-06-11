-- 20260528c_add_extra_to_node_provisioning_events.sql
--
-- Adds the `extra JSONB` column to node_provisioning_events. The column is
-- needed by the events.emit_event helper (T8 of the AWS EC2 node-allocation
-- refactor — see docs/plans/2026-05-28-aws-ec2-node-allocation.md) and the
-- EventLine dataclass it consumes. Existing writers
-- (NodeProvisioningRepo.append_event) omit the column; they continue to
-- work because of the NOT NULL DEFAULT.
--
-- Filename uses the `c` suffix to land alphabetically after the migrations
-- created in T1 (20260528a, 20260528b). cli_init.py applies migrations in
-- sorted filename order; this file runs after the queue table is in place.
--
-- The original 20260525_add_node_provisioning_events.sql cannot carry this
-- change in-place: cli_init.py tracks applied migrations by filename
-- (schema_migrations), so modifying the in-place file would silently
-- skip the change on environments that already applied the 2026-05-25
-- baseline. A separate dated file is the production-discipline correct
-- shape.

ALTER TABLE node_provisioning_events
    ADD COLUMN IF NOT EXISTS extra JSONB NOT NULL DEFAULT '{}'::jsonb;
