-- Migration: Add retention/cleanup support for append-only tables (#82)

-- Delete PUBLISHED outbox events older than 7 days (run periodically)
-- This is a helper function; call via pg_cron or application scheduler.
CREATE OR REPLACE FUNCTION cleanup_published_outbox_events(retention_days int DEFAULT 7)
RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
  deleted bigint;
BEGIN
  DELETE FROM outbox_events
  WHERE status = 'PUBLISHED'
    AND published_at < now() - make_interval(days => retention_days);
  GET DIAGNOSTICS deleted = ROW_COUNT;
  RETURN deleted;
END;
$$;

-- Delete DEAD outbox events older than 30 days
CREATE OR REPLACE FUNCTION cleanup_dead_outbox_events(retention_days int DEFAULT 30)
RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
  deleted bigint;
BEGIN
  DELETE FROM outbox_events
  WHERE status = 'DEAD'
    AND updated_at < now() - make_interval(days => retention_days);
  GET DIAGNOSTICS deleted = ROW_COUNT;
  RETURN deleted;
END;
$$;

-- Archive audit_logs older than 90 days to a partitioned archive
-- For now, just provide the cleanup function; partitioning can be added later.
CREATE OR REPLACE FUNCTION cleanup_old_audit_logs(retention_days int DEFAULT 90)
RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
  deleted bigint;
BEGIN
  DELETE FROM audit_logs
  WHERE timestamp < now() - make_interval(days => retention_days);
  GET DIAGNOSTICS deleted = ROW_COUNT;
  RETURN deleted;
END;
$$;

-- Cleanup billing_events older than 365 days
CREATE OR REPLACE FUNCTION cleanup_old_billing_events(retention_days int DEFAULT 365)
RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
  deleted bigint;
BEGIN
  DELETE FROM billing_events
  WHERE occurred_at < now() - make_interval(days => retention_days);
  GET DIAGNOSTICS deleted = ROW_COUNT;
  RETURN deleted;
END;
$$;
