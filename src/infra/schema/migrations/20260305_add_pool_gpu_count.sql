-- Migration: Add gpu_count to compute_pools
-- Allows specifying number of GPUs when provisioning cluster-based pools (e.g. SkyPilot)

ALTER TABLE compute_pools
ADD COLUMN IF NOT EXISTS gpu_count integer NOT NULL DEFAULT 1;
