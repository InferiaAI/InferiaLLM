-- Add org_id column to audit_logs table for organization-scoped audit tracking
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS org_id VARCHAR;
CREATE INDEX IF NOT EXISTS ix_audit_logs_org_id ON audit_logs (org_id);
