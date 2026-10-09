-- The mirror reads these to answer HuggingFace metadata without the internet.
-- huggingface_hub refuses a file whose HEAD carries no X-Repo-Commit or ETag,
-- so a cached model is unusable offline until the row can supply them.
ALTER TABLE model_cache
  ADD COLUMN IF NOT EXISTS commit_sha TEXT,
  ADD COLUMN IF NOT EXISTS file_meta  JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS pinned     BOOLEAN NOT NULL DEFAULT false;

-- huggingface_hub matches a revision against ^[0-9a-f]{40}$ before treating it
-- as a commit, so anything else here would never be looked up.
ALTER TABLE model_cache
  DROP CONSTRAINT IF EXISTS model_cache_commit_sha_format;
ALTER TABLE model_cache
  ADD CONSTRAINT model_cache_commit_sha_format
  CHECK (commit_sha IS NULL OR commit_sha ~ '^[0-9a-f]{40}$');

-- snapshot_download asks for /resolve/<commit>/..., so the mirror resolves a
-- 40-hex revision to its row by this column rather than by revision.
CREATE INDEX IF NOT EXISTS idx_model_cache_commit
  ON public.model_cache (source, model_id, commit_sha);

COMMENT ON COLUMN public.model_cache.file_meta IS
    'path -> {size, etag}. The mirror serves ETag and Content-Length from here on HEAD.';
COMMENT ON COLUMN public.model_cache.pinned IS
    'Excluded from LRU eviction. An imported model cannot be re-downloaded on an air-gapped install.';
