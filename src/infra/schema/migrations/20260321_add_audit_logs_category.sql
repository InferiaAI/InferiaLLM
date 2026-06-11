-- Add category column to audit_logs for filtering by event type
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS category VARCHAR;
CREATE INDEX IF NOT EXISTS ix_audit_logs_category ON audit_logs (category);

-- Backfill existing logs with derived categories
UPDATE audit_logs SET category = CASE
    WHEN action LIKE 'user.login%' THEN 'auth'
    WHEN action LIKE 'user.register%' THEN 'auth'
    WHEN action LIKE 'user.accept%' THEN 'auth'
    WHEN action LIKE 'user.switch%' THEN 'auth'
    WHEN action LIKE 'user.2fa%' THEN 'security'
    WHEN action LIKE 'user.create%' THEN 'user_management'
    WHEN action LIKE 'deployment.%' THEN 'deployment'
    WHEN action LIKE 'pool.%' THEN 'deployment'
    WHEN action LIKE 'api_key.%' THEN 'api_key'
    WHEN action LIKE 'organization.%' THEN 'organization'
    WHEN action LIKE 'invitation.%' THEN 'organization'
    WHEN action LIKE 'credential.%' THEN 'credential'
    WHEN action LIKE 'config.%' THEN 'configuration'
    WHEN action LIKE 'prompt_template.%' THEN 'configuration'
    WHEN action LIKE 'knowledge_base.%' THEN 'knowledge_base'
    ELSE split_part(action, '.', 1)
END WHERE category IS NULL;
