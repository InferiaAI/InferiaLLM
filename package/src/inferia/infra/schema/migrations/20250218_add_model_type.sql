-- Migration: Add model_type column to model_deployments table
-- Date: 2025-02-18
-- Purpose: Support for embedding models and extensible model types

-- Add model_type column with default 'inference' for backward compatibility
ALTER TABLE public.model_deployments 
ADD COLUMN IF NOT EXISTS model_type text DEFAULT 'inference';

-- Create index for filtering by model type
CREATE INDEX IF NOT EXISTS idx_model_deployments_model_type
    ON public.model_deployments USING btree
    (model_type COLLATE pg_catalog."default" ASC NULLS LAST);

-- Create enum type for model types (optional - for stricter type safety)
-- Uncomment if you want to use enum instead of text
-- CREATE TYPE model_type_enum AS ENUM (
--     'inference',
--     'embedding', 
--     'training',
--     'batch',
--     'image_generation',
--     'video_generation',
--     'audio_generation',
--     'multimodal'
-- );

-- Update existing records to have explicit model_type based on engine
UPDATE public.model_deployments 
SET model_type = 'inference' 
WHERE model_type IS NULL OR model_type = '';
