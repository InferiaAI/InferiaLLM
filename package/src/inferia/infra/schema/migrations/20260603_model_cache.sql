-- 20260603_model_cache.sql (idempotent)
-- Global model cache index. Files live on the modelcache volume; this row
-- tracks status/size/usage for the Models tab + LRU eviction.
CREATE TABLE IF NOT EXISTS public.model_cache (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source       text NOT NULL,                 -- 'hf' | 'ollama'
    model_id     text NOT NULL,                 -- HF 'org/repo' or ollama 'name'
    revision     text NOT NULL DEFAULT 'main',  -- HF revision or ollama tag
    engine_hint  text,
    status       text NOT NULL DEFAULT 'pending',-- pending|downloading|cached|error
    bytes_total  bigint NOT NULL DEFAULT 0,
    bytes_done   bigint NOT NULL DEFAULT 0,
    error        text,
    last_used_at timestamptz NOT NULL DEFAULT now(),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT model_cache_uniq UNIQUE (source, model_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_model_cache_lru ON public.model_cache (last_used_at ASC);
