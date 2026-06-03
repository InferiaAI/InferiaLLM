"""DownloadManager — pre-warm the model cache.

For HuggingFace models, enumerates the repo's file list via the HF API and
fetches each file into the cache directory, updating ``bytes_done``/``status``
on the ``model_cache`` row as it goes.

Concurrency dedup: one task per ``(source, model_id, revision)`` key.
Re-triggering while the task is still running joins the in-flight task.

Pre-warm failure is NON-FATAL: the row is marked ``status='error'`` and no
exception is propagated to the caller.

For ``source != 'hf'`` (e.g. ``ollama``) this phase is a no-op placeholder;
full support is added in Phase 9.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("inferia.model_cache.downloader")


class DownloadManager:
    def __init__(self, *, repo, paths, fetch_list=None, fetch_file=None, http_client=None, settings=None):
        self.repo = repo
        self.paths = paths
        self.http_client = http_client
        self.settings = settings
        self._fetch_list = fetch_list or self._hf_list
        self._fetch_file = fetch_file or self._hf_file
        self._tasks: dict[tuple, asyncio.Task] = {}

    def start(self, *, source, model_id, revision="main", engine_hint=None):
        """Fire-and-forget pre-warm; deduplicates by ``(source, model_id, revision)`` key.

        Returns the asyncio.Task for the running pre-warm operation.  If a
        task for the same key is already in progress it is returned unchanged;
        a new task is only created after the previous one has finished.
        """
        key = (source, model_id, revision)
        t = self._tasks.get(key)
        if t and not t.done():
            return t
        t = asyncio.create_task(
            self.prewarm(source=source, model_id=model_id, revision=revision, engine_hint=engine_hint)
        )
        self._tasks[key] = t
        return t

    async def prewarm(self, *, source, model_id, revision="main", engine_hint=None):
        """Download all files for *model_id* at *revision* into the cache.

        Failure is caught and recorded as ``status='error'`` on the row; the
        exception is never propagated to callers.
        """
        row = await self.repo.upsert(
            source=source, model_id=model_id, revision=revision, engine_hint=engine_hint
        )
        cid = row["id"]
        try:
            if source != "hf":
                # Ollama and other sources handled in Phase 9 — treat as cached.
                await self.repo.set_status(cid, "cached")
                return

            files = await self._fetch_list(model_id, revision)
            total = sum(int(f.get("size") or 0) for f in files)
            done = 0
            last_reported = 0
            await self.repo.set_progress(cid, bytes_total=total, bytes_done=0, status="downloading")

            async def on_bytes(n):
                # Accumulate real downloaded bytes; throttle DB writes to ~every
                # 8 MB so a multi-GB file doesn't issue an UPDATE per chunk.
                nonlocal done, last_reported
                done += n
                if done - last_reported >= 8 * 1024 * 1024:
                    last_reported = done
                    await self.repo.set_progress(cid, bytes_done=done)

            for f in files:
                await self._fetch_file(model_id, revision, f["path"], on_bytes)

            # Record the ACTUAL downloaded total (do not clobber with the
            # pre-computed `total`, which is 0 when the listing lacked sizes).
            await self.repo.set_progress(
                cid, bytes_total=max(total, done), bytes_done=done, status="cached"
            )

        except Exception as e:  # pre-warm failure is non-fatal to deploys
            logger.warning("prewarm failed %s/%s: %s", model_id, revision, e)
            await self.repo.set_status(cid, "error", str(e))

    # ------------------------------------------------------------------
    # Default HF implementations (used when no injection is supplied)
    # ------------------------------------------------------------------

    async def _hf_list(self, model_id, revision):
        """Fetch the file list (with real sizes) for *model_id* at *revision*.

        Uses the HF *tree* API (``/api/models/{repo}/tree/{rev}?recursive=true``)
        rather than the model-info ``siblings`` list, because ``siblings`` only
        carries ``rfilename`` (no size) — which left ``bytes_total`` at 0 and
        prevented any progress reporting. The tree API returns a per-file
        ``size`` (the resolved content size, including LFS objects).
        """
        import json

        url = (
            f"https://huggingface.co/api/models/{model_id}/tree/{revision}"
            "?recursive=true"
        )
        async with self.http_client.stream("GET", url, headers=self._hdr()) as up:
            if up.status_code != 200:
                raise RuntimeError(
                    f"HF tree listing failed for {model_id}@{revision} "
                    f"(HTTP {up.status_code})"
                )
            data = json.loads(b"".join([c async for c in up.aiter_bytes()]).decode())

        files = []
        for entry in data:
            if entry.get("type") != "file":
                continue  # skip directories
            size = entry.get("size")
            lfs = entry.get("lfs")
            if (size is None or size == 0) and isinstance(lfs, dict):
                size = lfs.get("size")
            files.append({"path": entry["path"], "size": int(size or 0)})
        return files

    async def _hf_file(self, model_id, revision, path, on_bytes):
        """Download a single HF file into the cache; skip if already present."""
        target = self.paths.hf_dir(model_id, revision) / path
        if target.is_file():
            await on_bytes(target.stat().st_size)
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        url = f"https://huggingface.co/{model_id}/resolve/{revision}/{path}"
        async with self.http_client.stream("GET", url, headers=self._hdr()) as up:
            # MUST check status BEFORE writing — a gated/private repo returns a
            # small 401/403 body which would otherwise be saved to disk as if it
            # were the real weights (and the model wrongly marked "cached").
            if up.status_code != 200:
                if up.status_code in (401, 403):
                    raise RuntimeError(
                        f"{model_id} is gated or private; set INFERIA_HF_TOKEN on "
                        f"the control plane to cache it (HTTP {up.status_code})"
                    )
                raise RuntimeError(
                    f"download failed for {path} (HTTP {up.status_code})"
                )
            with open(tmp, "wb") as fh:
                async for chunk in up.aiter_bytes():
                    fh.write(chunk)
                    await on_bytes(len(chunk))
        os.replace(tmp, target)

    def _hdr(self) -> dict:
        tok = getattr(self.settings, "hf_token", "") if self.settings else ""
        return {"authorization": f"Bearer {tok}"} if tok else {}
