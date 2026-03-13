-- Migration: Add media logging columns to inference_logs
-- Date: 2026-03-13
-- Description: Adds request_type and media_metadata columns to support
--              image, video, audio generation, and multimodal request logging.

-- Add request_type column (defaults to 'llm' for backward compatibility)
ALTER TABLE inference_logs ADD COLUMN IF NOT EXISTS request_type TEXT DEFAULT 'llm';

-- Add media_metadata JSONB column for type-specific metrics
-- (image: size/n/quality, video: duration/fps, audio: voice/format, etc.)
ALTER TABLE inference_logs ADD COLUMN IF NOT EXISTS media_metadata JSONB;

-- Index for filtering logs by request type
CREATE INDEX IF NOT EXISTS idx_inference_logs_request_type ON inference_logs(request_type);
