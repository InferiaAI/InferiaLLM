ALTER TABLE model_deployments
  ADD COLUMN IF NOT EXISTS auto_replica_enabled BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS tokens_per_second_threshold DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS auto_replica_last_scale_at TIMESTAMP WITHOUT TIME ZONE;
