-- Migration: per-pool inference_token for inferia-worker integration
--
-- Workers in a pool share a single inference_token (sent as
-- `Authorization: Bearer <token>` on every inference request that the
-- control plane forwards to a worker). The token is generated lazily on
-- first mint of a worker bootstrap token; pools that never host workers
-- never get one.

ALTER TABLE compute_pools
ADD COLUMN IF NOT EXISTS inference_token text;

-- Forward-only: no backfill (NULL means 'no workers in this pool yet').
