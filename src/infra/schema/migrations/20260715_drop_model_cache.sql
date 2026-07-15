-- 20260715_drop_model_cache.sql (idempotent)
-- The control-plane model cache / mirror feature was removed: engine nodes
-- pull model weights straight from origin (huggingface.co / OCI registry)
-- instead of through the CP HF mirror + /v2 registry. Drop its index table.
-- Cached files on the modelcache volume are NOT deleted here — remove the
-- volume/directory out-of-band if reclaiming disk.
DROP INDEX IF EXISTS idx_model_cache_lru;
DROP TABLE IF EXISTS public.model_cache;
