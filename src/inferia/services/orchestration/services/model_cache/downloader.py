"""DownloadManager — pre-warm the model cache.

For HuggingFace models, enumerates the repo's file list via the HF API and
fetches each file into the cache directory, updating ``bytes_done``/``status``
on the ``model_cache`` row as it goes.

For Ollama models, fetches the manifest from the Ollama registry to get the
list of blobs (config + layers), then streams each blob to the per-model
ollama dir in the cache.

Concurrency dedup: one task per ``(source, model_id, revision)`` key.
Re-triggering while the task is still running joins the in-flight task.

Pre-warm failure is NON-FATAL: the row is marked ``status='error'`` and no
exception is propagated to the caller.

For ``source`` values other than ``hf`` or ``ollama``, this phase is a no-op
(marks "cached" without downloading).
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("inferia.model_cache.downloader")

# Ollama registry base URL
_OLLAMA_REGISTRY = "https://registry.ollama.ai"
_OLLAMA_MANIFEST_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"


def _ollama_name(model_id: str) -> str:
    """Return the Ollama registry name for *model_id*.

    If *model_id* contains no ``/``, prefix with ``library/``.
    """
    return model_id if "/" in model_id else f"library/{model_id}"


class DownloadManager:
    def __init__(self, *, repo, paths, fetch_list=None, fetch_file=None, http_client=None, settings=None):
        self.repo = repo
        self.paths = paths
        self.http_client = http_client
        self.settings = settings
        self._fetch_list = fetch_list or self._hf_list
        self._fetch_file = fetch_file or self._hf_file
        self._tasks: dict[tuple, asyncio.Task] = {}
        self._hf_token: str = ""

    async def _load_hf_token(self) -> str:
        """Return the HF token: DB-stored provider config first, then env fallback.

        Loads the provider config from the database (same path as the Pulumi
        adapter) to retrieve ``providers.huggingface.token``.  Falls back to
        ``settings.hf_token`` (i.e. ``INFERIA_HF_TOKEN`` env var) if the DB
        record is absent, the DB is unreachable, or the stored token is empty.
        """
        try:
            from inferia.services.api_gateway.db.database import AsyncSessionLocal
            from inferia.services.api_gateway.management.config_manager import config_manager
            async with AsyncSessionLocal() as db:
                data = await config_manager.load_config(db) or {}
            tok = ((data.get("providers") or {}).get("huggingface") or {}).get("token") or ""
        except Exception as e:
            logger.warning(
                "could not load HF token from provider config (%s); "
                "falling back to INFERIA_HF_TOKEN env", e
            )
            tok = ""
        if tok:
            return tok
        return getattr(self.settings, "hf_token", "") if self.settings else ""

    def cancel(self, *, source, model_id, revision="main") -> bool:
        """Cancel an in-flight pre-warm for this key, if one is running.

        Returns True if a running task was cancelled. Used by the delete
        endpoint so removing a model mid-download actually stops the transfer
        (asyncio.CancelledError unwinds the open file/stream; the caller then
        removes the partial files + row).
        """
        key = (source, model_id, revision)
        t = self._tasks.get(key)
        if t is None:
            return False
        running = not t.done()
        if running:
            t.cancel()
        # Always drop the key — leaving a *finished* task in the dict makes a
        # subsequent re-add of the same model silently join the stale (done)
        # task instead of starting a fresh download.
        self._tasks.pop(key, None)
        return running

    async def await_key(self, *, source, model_id, revision="main") -> None:
        """Await the in-flight pre-warm task for this key if one is running.

        Used by the mirror handlers: when a worker requests an artifact the CP
        is still downloading, block until the pre-warm finishes (then the file
        is on disk) instead of re-streaming the same bytes from origin. No-op if
        there is no task or it is already done. A failed task is swallowed — the
        caller sees the (now 'error') cache state and falls back to origin.
        """
        t = self._tasks.get((source, model_id, revision))
        if t and not t.done():
            try:
                await t
            except BaseException:
                pass

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

        Dispatches to the appropriate downloader based on *source*:
        - ``hf``: HuggingFace (uses injectable ``_fetch_list``/``_fetch_file``)
        - ``ollama``: Ollama registry (uses ``_ollama_list``/``_ollama_file``)
        - anything else: no-op, marks directly as cached

        Failure is caught and recorded as ``status='error'`` on the row; the
        exception is never propagated to callers.
        """
        row = await self.repo.upsert(
            source=source, model_id=model_id, revision=revision, engine_hint=engine_hint
        )
        cid = row["id"]
        try:
            if source == "hf":
                # Resolve token once per pre-warm — avoids per-file DB round-trips
                self._hf_token = await self._load_hf_token()
                await self._run_prewarm(
                    cid,
                    list_fn=self._fetch_list,
                    file_fn=self._fetch_file,
                    model_id=model_id,
                    revision=revision,
                )
            elif source == "ollama":
                await self._run_prewarm(
                    cid,
                    list_fn=self._ollama_list,
                    file_fn=self._ollama_file,
                    model_id=model_id,
                    revision=revision,
                )
            else:
                # Unknown source — treat as cached (no download needed).
                await self.repo.set_status(cid, "cached")

        except Exception as e:  # pre-warm failure is non-fatal to deploys
            logger.warning("prewarm failed %s/%s: %s", model_id, revision, e)
            await self.repo.set_status(cid, "error", str(e))

    # ------------------------------------------------------------------
    # Shared download loop — both HF and Ollama use this
    # ------------------------------------------------------------------

    async def _run_prewarm(self, cid, *, list_fn, file_fn, model_id, revision):
        """Enumerate files via *list_fn*, download each via *file_fn*, update progress.

        Progress is throttled to ~every 8 MB to avoid hammering the DB on
        large (multi-GB) weight files.  The final ``set_progress`` call
        records the ACTUAL downloaded total.
        """
        files = await list_fn(model_id, revision)
        total = sum(int(f.get("size") or 0) for f in files)
        done = 0
        last_reported = 0
        await self.repo.set_progress(cid, bytes_total=total, bytes_done=0, status="downloading")

        async def on_bytes(n):
            nonlocal done, last_reported
            done += n
            if done - last_reported >= 8 * 1024 * 1024:
                last_reported = done
                await self.repo.set_progress(cid, bytes_done=done)

        for f in files:
            await file_fn(model_id, revision, f["path"], on_bytes)

        # Record the ACTUAL downloaded total (do not clobber with the
        # pre-computed `total`, which is 0 when the listing lacked sizes).
        await self.repo.set_progress(
            cid, bytes_total=max(total, done), bytes_done=done, status="cached"
        )

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
            try:
                with open(tmp, "wb") as fh:
                    async for chunk in up.aiter_bytes():
                        fh.write(chunk)
                        await on_bytes(len(chunk))
            except BaseException:
                # On cancel (delete-mid-download) or any stream error, the
                # ``.part`` file is never promoted — remove it so it doesn't
                # leak disk and count against the cache size cap. BaseException
                # so asyncio.CancelledError is caught too; re-raised below.
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        os.replace(tmp, target)

    def _hdr(self) -> dict:
        """Return auth header for HF requests using the resolved token.

        ``self._hf_token`` is set once in ``prewarm`` before HF downloads
        begin via ``_load_hf_token()``.  The method remains sync so callers
        don't need to await it in the stream context.
        """
        tok = self._hf_token
        return {"authorization": f"Bearer {tok}"} if tok else {}

    # ------------------------------------------------------------------
    # Ollama registry implementations
    # ------------------------------------------------------------------

    async def _ollama_list(self, model_id: str, revision: str) -> list[dict]:
        """Fetch the Ollama manifest and return a list of ``{path, size}`` dicts.

        ``path`` is the blob digest (e.g. ``sha256:...``).  The config blob
        and every layer are included.  No auth header is needed — the Ollama
        registry is public.
        """
        import json

        name = _ollama_name(model_id)
        url = f"{_OLLAMA_REGISTRY}/v2/{name}/manifests/{revision}"
        async with self.http_client.stream(
            "GET", url, headers={"Accept": _OLLAMA_MANIFEST_ACCEPT}
        ) as up:
            if up.status_code != 200:
                raise RuntimeError(
                    f"Ollama manifest fetch failed for {model_id}:{revision} "
                    f"(HTTP {up.status_code})"
                )
            data = json.loads(b"".join([c async for c in up.aiter_bytes()]).decode())

        # Persist the raw manifest so the /v2 mirror can serve it back to a
        # worker (ollama pull manifests/{tag}). Best-effort: a failure here
        # doesn't break the blob enumeration / download.
        if self.paths is not None:
            try:
                mdir = self.paths.ollama_dir(model_id, revision)
                mdir.mkdir(parents=True, exist_ok=True)
                (mdir / "manifest.json").write_text(json.dumps(data))
            except OSError:
                pass

        blobs: list[dict] = []
        # Config blob
        cfg = data.get("config", {})
        if cfg.get("digest"):
            blobs.append({"path": cfg["digest"], "size": int(cfg.get("size") or 0)})
        # Layer blobs
        for layer in data.get("layers", []):
            if layer.get("digest"):
                blobs.append({"path": layer["digest"], "size": int(layer.get("size") or 0)})
        return blobs

    async def _ollama_file(self, model_id: str, revision: str, path: str, on_bytes) -> None:
        """Download a single Ollama blob to the per-model cache dir.

        *path* is the blob digest (e.g. ``sha256:abc123``).  The local
        filename replaces ``:`` with ``_`` so it is safe on all filesystems.
        Already-present blobs are skipped (on_bytes called with existing size).
        Downloads go to a ``.part`` file first; ``os.replace`` makes the
        rename atomic.
        """
        # Sanitize digest for filename: "sha256:abc..." → "sha256_abc..."
        blob_filename = path.replace(":", "_")
        target = self.paths.ollama_dir(model_id, revision) / blob_filename

        if target.is_file():
            await on_bytes(target.stat().st_size)
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")

        name = _ollama_name(model_id)
        url = f"{_OLLAMA_REGISTRY}/v2/{name}/blobs/{path}"
        # No auth header — Ollama registry is public
        async with self.http_client.stream("GET", url) as up:
            if up.status_code != 200:
                raise RuntimeError(
                    f"Ollama blob download failed for {path} (HTTP {up.status_code})"
                )
            try:
                with open(tmp, "wb") as fh:
                    async for chunk in up.aiter_bytes():
                        fh.write(chunk)
                        await on_bytes(len(chunk))
            except BaseException:
                # On cancel (delete-mid-download) or any stream error, drop the
                # unfinished ``.part`` blob so it doesn't leak disk / count
                # against the cache size cap. BaseException catches CancelledError.
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        os.replace(tmp, target)
