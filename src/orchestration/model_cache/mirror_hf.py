"""HF pull-through mirror router.

Routes
------
GET /hf/api/{rest:path}
    Proxy HuggingFace API metadata straight through (not cached).

GET /hf/{repo:path}/resolve/{rev}/{filename:path}
    Serve from disk on a cache hit; otherwise stream from HuggingFace,
    tee to disk (atomic publish via .part → rename), and stream to client.

Design notes
------------
* **Pre-flight status check** — for the resolve endpoint, the upstream
  connection is opened and ``status_code`` is inspected *before* the
  ``StreamingResponse`` is returned to FastAPI.  If the upstream returns
  anything other than 200 we raise ``HTTPException`` immediately (the
  response headers have not been sent yet), ensuring the caller gets a
  proper 4xx/5xx rather than a garbled 200 stream.

* **Atomic publish** — body bytes are written to ``<target>.part`` while
  streaming.  ``os.replace`` (rename) is called only after the generator
  is fully exhausted.  On any upstream error the ``.part`` file is removed
  (if it exists) so no partial file is ever promoted to the cache.

* **Path-traversal guard** — the resolved ``target`` path must stay under
  the per-repo cache directory returned by ``CachePaths.hf_dir``.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from . import deps

router = APIRouter(prefix="/hf", tags=["hf-mirror"])

_HF = "https://huggingface.co"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _client():
    return deps.get("http_client")


def _hf_headers() -> dict:
    s = deps.get("settings")
    tok = getattr(s, "hf_token", "") if s else ""
    return {"authorization": f"Bearer {tok}"} if tok else {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.head("/{repo:path}/resolve/{rev}/{filename:path}")
async def head_file(repo: str, rev: str, filename: str) -> Response:
    """Answer huggingface_hub's metadata HEAD on the resolve URL.

    Before downloading, huggingface_hub HEADs the resolve URL to read ETag,
    X-Repo-Commit and the size. Without this route FastAPI returned 405 and the
    vLLM/TEI container's metadata resolution failed — so the mirror (and the
    whole HF cache-first path) was never used. We proxy HF's HEAD (small
    metadata call) and reply 200 with the headers huggingface_hub needs; the
    big file itself is still served from the local cache on the follow-up GET.
    """
    from urllib.parse import urljoin

    url = f"{_HF}/{repo}/resolve/{rev}/{filename}"
    headers: dict[str, str] = {"accept-ranges": "bytes"}

    # Is this file already in the local cache? If so its on-disk size is the
    # AUTHORITATIVE Content-Length: the follow-up GET serves exactly these bytes
    # via FileResponse. We must NOT trust HF's resolve-HEAD size here — for
    # non-LFS files HF answers with a 307 whose Content-Length is the redirect
    # body (not the file) and carries NO X-Linked-Size, so our extraction can
    # land on 0. A Content-Length of 0 makes huggingface_hub's http_get skip the
    # download entirely (resume_size == expected_size == 0) and write an EMPTY
    # file -> the engine then parses empty JSON and dies with "Expecting value:
    # line 1 column 1 (char 0)" (seen live for tokenizer_config.json /
    # vocab.json on a vLLM deploy).
    cached_size: str | None = None
    cp = deps.get("paths")
    if cp is not None:
        try:
            base = cp.hf_dir(repo, rev)
            target = base / filename
            if target.resolve().is_relative_to(base.resolve()) and target.is_file():
                cached_size = str(target.stat().st_size)
        except Exception:
            cached_size = None

    try:
        # First hop, no redirect. HF answers resolve HEADs with a 302/307 to the
        # actual blob (CDN for LFS, /api/resolve-cache for small files) and
        # carries the metadata huggingface_hub needs on THIS response:
        #   X-Repo-Commit, X-Linked-Etag (the per-file etag), X-Linked-Size.
        async with _client().stream(
            "HEAD", url, headers=_hf_headers(), follow_redirects=False,
        ) as r0:
            status0 = r0.status_code
            commit = r0.headers.get("x-repo-commit")
            etag = r0.headers.get("x-linked-etag") or r0.headers.get("etag")
            size = r0.headers.get("x-linked-size")
            location = r0.headers.get("location")
            if not size and r0.status_code == 200:
                size = r0.headers.get("content-length")
        # A genuine 404 (with nothing cached) must surface as 404 so the engine's
        # optional-file probes (special_tokens_map.json / preprocessor_config.json
        # on a text model) resolve as "absent" — not a fake 200 whose GET then
        # 404s, an inconsistency that confuses some clients.
        if status0 == 404 and cached_size is None:
            return Response(status_code=404)
        # If the size wasn't on the first hop (small non-LFS files), follow the
        # redirect for Content-Length. The Location is often RELATIVE
        # (/api/resolve-cache/...) — resolve it against huggingface.co, else
        # httpx raises on a relative URL and we lose the metadata.
        if not size and location:
            try:
                async with _client().stream(
                    "HEAD", urljoin(_HF + "/", location), headers=_hf_headers(),
                    follow_redirects=True,
                ) as r1:
                    size = r1.headers.get("content-length") or size
                    etag = etag or r1.headers.get("etag")
            except Exception:
                pass  # size is optional; commit+etag already captured
        # Cached on-disk size wins over whatever HF reported (see above).
        if cached_size is not None:
            size = cached_size
        # Reply 200 (NOT a redirect) so huggingface_hub treats the mirror URL as
        # the download source and GETs it from us (served from cache).
        if commit:
            headers["x-repo-commit"] = commit
        if etag:
            headers["etag"] = etag
        if size:
            headers["content-length"] = size
        return Response(status_code=200, headers=headers)
    except Exception:
        # Metadata HEAD is best-effort. If we at least know the cached size,
        # still report it so huggingface_hub downloads the real bytes.
        if cached_size is not None:
            headers["content-length"] = cached_size
        return Response(status_code=200, headers=headers)


@router.get("/api/{rest:path}")
async def proxy_api(rest: str, request: Request) -> Response:
    """Proxy HuggingFace API metadata straight through (not cached)."""
    url = f"{_HF}/api/{rest}"
    if request.url.query:
        url += f"?{request.url.query}"
    async with _client().stream("GET", url, headers=_hf_headers()) as up:
        body = b"".join([chunk async for chunk in up.aiter_bytes()])
        return Response(
            content=body,
            status_code=up.status_code,
            media_type=up.headers.get("content-type", "application/json"),
        )


@router.get("/{repo:path}/resolve/{rev}/{filename:path}")
async def resolve_file(
    repo: str, rev: str, filename: str, request: Request
) -> Response:
    """Serve a model file from the local cache or pull it from HuggingFace.

    Cache hits return immediately via ``FileResponse``.  Cache misses open
    an upstream connection, **check the status code before committing to a
    ``StreamingResponse``**, stream the body to the client while writing to
    a ``.part`` temp, and atomically rename on completion.
    """
    cp = deps.get("paths")
    base: Path = cp.hf_dir(repo, rev)
    target: Path = base / filename

    # ------------------------------------------------------------------
    # Path-traversal guard
    # ------------------------------------------------------------------
    # Use Path.is_relative_to (Python 3.11+) after resolving both sides.
    # os.path.normpath + startswith is insufficient: a filename like
    # "../mainleak/x" normalises to a path whose string starts with the
    # base string when base ends without a separator (e.g. ".../main" is a
    # prefix of ".../mainleak/x").  is_relative_to performs a proper
    # parent-directory containment check, not a string prefix match.
    # NOTE: target may not exist yet, but Path.resolve() on Python 3.6+
    # handles non-existent paths by resolving as far as possible; combined
    # with is_relative_to this is safe.
    if not target.resolve().is_relative_to(base.resolve()):
        raise HTTPException(400, "bad path")

    # ------------------------------------------------------------------
    # Cache hit
    # ------------------------------------------------------------------
    if target.is_file():
        repo_obj = deps.get("repo")
        if repo_obj:
            await repo_obj.touch_by_key(source="hf", model_id=repo, revision=rev)
        return FileResponse(str(target))

    # ------------------------------------------------------------------
    # Cache miss — first join any in-flight pre-warm for this model. If the
    # CP is downloading these weights, wait for that to finish and serve from
    # disk instead of re-streaming the same bytes from origin.
    # ------------------------------------------------------------------
    dl = deps.get("downloader")
    if dl is not None:
        await dl.await_key(source="hf", model_id=repo, revision=rev)
        if target.is_file():
            repo_obj = deps.get("repo")
            if repo_obj:
                await repo_obj.touch_by_key(source="hf", model_id=repo, revision=rev)
            return FileResponse(str(target))

    # ------------------------------------------------------------------
    # Cache miss — pre-flight upstream check then stream
    # ------------------------------------------------------------------
    url = f"{_HF}/{repo}/resolve/{rev}/{filename}"
    tmp = target.with_suffix(target.suffix + ".part")

    # Open the upstream connection and inspect status BEFORE returning the
    # StreamingResponse.  This guarantees that if the upstream returns a
    # non-200 we can raise HTTPException while headers have not yet been sent.
    upstream_ctx = _client().stream("GET", url, headers=_hf_headers())
    up = await upstream_ctx.__aenter__()

    if up.status_code != 200:
        # Clean exit: close the upstream and raise before any disk write.
        # Do NOT create any directories — nothing should be written on non-200.
        await upstream_ctx.__aexit__(None, None, None)
        raise HTTPException(up.status_code, "upstream error")

    # Upstream is 200 — define the streaming generator that holds the open
    # connection and writes to disk, then publishes atomically.
    async def _gen():
        try:
            # Only create the directory after upstream has confirmed 200.
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "wb") as fh:
                async for chunk in up.aiter_bytes():
                    fh.write(chunk)
                    yield chunk
            # Atomic publish: only reached on full success.
            os.replace(tmp, target)
        except Exception:
            # On any error, clean up the .part temp so it is never promoted.
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise
        finally:
            await upstream_ctx.__aexit__(None, None, None)

    return StreamingResponse(_gen(), media_type="application/octet-stream")
