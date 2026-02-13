-- Adds client IP tracking for inference logs and supports Insights filtering by IP.
ALTER TABLE inference_logs
ADD COLUMN IF NOT EXISTS ip_address VARCHAR;

CREATE INDEX IF NOT EXISTS ix_inference_logs_ip_address
ON inference_logs (ip_address);
