-- SQL seed for the inferiallm-dashboard OAuth client.
-- Run after `migrate` and BEFORE inferia-auth boots in the SSO compose.
--
-- Why this exists: the inferia-auth boot-time seed in cmd/server/seed.go
-- both inserts this row AND seeds the FGA permission tree. The FGA part
-- currently fails on real OpenFGA backends because permission IDs contain
-- colons (e.g. "inferiallm:deployment:read") which the OpenFGA tuple-user
-- validator rejects. That's a Phase A bug tracked separately.
--
-- To bring up the SSO compose without that crash, we:
--   1. Set OAUTH_SEED_DISABLED=true on inferia-auth (skip the in-process seed)
--   2. Insert this row from SQL instead (this file)
--
-- The smoke test only needs the client row to exist; it doesn't require
-- the FGA app/permission tree (roles/permissions claims default to empty).
--
-- Idempotent: ON CONFLICT DO NOTHING so re-applying is safe.

INSERT INTO oauth_clients (
  id,
  client_id,
  client_name,
  app_namespace,
  client_type,
  redirect_uris,
  allowed_scopes,
  created_at
) VALUES (
  '00000000-0000-0000-0000-000000000001',
  'inferiallm-dashboard',
  'InferiaLLM Dashboard',
  'inferiallm',
  'public',
  ARRAY['https://inferia.local/auth/callback'],
  ARRAY['openid','profile','email','inferiallm'],
  NOW()
)
ON CONFLICT (client_id) DO NOTHING;
