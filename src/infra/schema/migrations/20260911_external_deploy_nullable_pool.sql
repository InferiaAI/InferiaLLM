-- src/infra/schema/migrations/20260911_external_deploy_nullable_pool.sql
--
-- External provider deployments have no compute pool. The foreign key stays,
-- so real pool references are still checked.

ALTER TABLE model_deployments
  ALTER COLUMN pool_id DROP NOT NULL;
