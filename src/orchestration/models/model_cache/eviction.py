"""EvictionManager — size-cap LRU eviction with in-use guard.

When total cache bytes exceed ``max_bytes``, evict least-recently-used cached
models until the cache fits within the cap.  Models whose ``model_id`` is in
the current in-use set (i.e. referenced by non-terminal deployments) are
NEVER evicted — ``repo.lru_candidates`` already excludes them at the query
level, so the loop never even sees those rows.

Eviction of a row means:
1. Remove the directory from disk (``shutil.rmtree``, errors silently ignored).
2. Delete the DB row (``repo.delete``).
3. Re-check ``paths.total_bytes()`` — stop once under the cap.
"""
from __future__ import annotations

import shutil
import logging

logger = logging.getLogger("model_cache.eviction")


class EvictionManager:
    """Evict least-recently-used models when the cache exceeds ``max_bytes``.

    Parameters
    ----------
    repo:
        A ``ModelCacheRepo`` instance (or compatible fake for testing).
    paths:
        A ``CachePaths`` instance (or compatible fake for testing).
    max_bytes:
        The size cap in bytes.  When ``paths.total_bytes()`` exceeds this,
        eviction runs.
    in_use:
        A zero-argument callable that returns the current ``set[model_id]``
        of models referenced by non-terminal deployments.  Called once per
        ``run_once`` invocation so the snapshot is consistent throughout the
        eviction pass.
    """

    def __init__(self, *, repo, paths, max_bytes: int, in_use):
        self.repo = repo
        self.paths = paths
        self.max_bytes = max_bytes
        self._in_use = in_use  # callable -> set[model_id]

    async def run_once(self) -> None:
        """Run one eviction pass: evict LRU models until under the cap (or exhausted)."""
        if self.paths.total_bytes() <= self.max_bytes:
            return

        in_use = self._in_use()
        for row in await self.repo.lru_candidates(exclude_model_ids=in_use):
            d = self._dir_for(row)
            shutil.rmtree(d, ignore_errors=True)
            await self.repo.delete(row["id"])
            logger.info("evicted %s/%s", row["model_id"], row["revision"])
            if self.paths.total_bytes() <= self.max_bytes:
                break

    def _dir_for(self, row: dict):
        """Return the filesystem directory for *row*.

        Returns the per-model directory so that eviction/delete removes only
        the blobs for this specific model rather than wiping the entire source
        cache.
        """
        if row["source"] == "hf":
            return self.paths.hf_dir(row["model_id"], row["revision"])
        if row["source"] == "ollama":
            # Use per-model dir so a single eviction doesn't wipe ALL ollama
            # models from disk.
            return self.paths.ollama_dir(row["model_id"], row["revision"])
        # Fallback for any other source: use the per-model ollama-style dir if
        # the paths object supports it, otherwise fall back to ollama_root.
        return self.paths.ollama_root()
