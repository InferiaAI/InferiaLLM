-- Migration: Add pool_type and cluster_id to compute_pools
-- This enables cluster-based provisioning for SkyPilot

-- Add pool_lifecycle_type enum if not exists
DO $$ BEGIN
    CREATE TYPE pool_lifecycle_type AS ENUM ('job', 'cluster');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Add new columns to compute_pools (if they don't exist)
ALTER TABLE compute_pools 
ADD COLUMN IF NOT EXISTS pool_type pool_lifecycle_type NOT NULL DEFAULT 'job';

ALTER TABLE compute_pools 
ADD COLUMN IF NOT EXISTS cluster_id text;

ALTER TABLE compute_pools 
ADD COLUMN IF NOT EXISTS region_constraint text[];

-- Add skypilot to provider_type if not exists  
DO $$ BEGIN
    ALTER TYPE provider_type ADD VALUE 'skypilot';
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;
