-- The inference app gives every request an X-Request-ID (#442). Storing it on
-- the log row lets a request seen elsewhere, in a client's error or a log
-- line, be found here. Rows logged before this keep it null.
--
-- inference_logs is often the largest table, so the index is built
-- CONCURRENTLY and does not block inserts. That cannot run inside a
-- transaction, so the runner executes the part after @SPLIT@ on its own.
ALTER TABLE inference_logs
  ADD COLUMN IF NOT EXISTS request_id VARCHAR;

-- @SPLIT@

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_inference_logs_request_id
  ON inference_logs (request_id);
