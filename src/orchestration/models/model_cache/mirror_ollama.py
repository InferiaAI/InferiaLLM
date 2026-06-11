"""Ollama /v2 OCI registry mirror.

Lets a worker `ollama pull <cp-host>/library/<name>:<tag>` fetch from the CP
cache. Serves the persisted manifest.json and per-digest blobs; on a miss it
first joins any in-flight pre-warm (await_key), then serves from disk; only if
still missing does it stream from registry.ollama.ai and cache.

Cache key mapping: the registry name `library/<name>` (or `<ns>/<name>`) maps to
the cache model_id by stripping a leading `library/`; the manifest ref is the
cache revision (tag). Mirrors downloader._ollama_name / ollama_dir layout.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from . import deps

router = APIRouter(prefix="/v2", tags=["ollama-mirror"])

_OLLAMA = "https://registry.ollama.ai"
_MANIFEST_CT = "application/vnd.docker.distribution.manifest.v2+json"


def _model_id(name: str) -> str:
    """Registry name -> cache model_id (strip a leading 'library/')."""
    return name[len("library/"):] if name.startswith("library/") else name


@router.get("")
@router.get("/")
async def root() -> Response:
    """Registry probe — ollama checks /v2/ returns 200 before pulling."""
    return Response(content=b"{}", media_type="application/json")


@router.get("/{name:path}/manifests/{ref}")
async def get_manifest(name: str, ref: str) -> Response:
    paths = deps.get("paths")
    model_id = _model_id(name)
    mpath: Path = paths.ollama_dir(model_id, ref) / "manifest.json"

    if mpath.is_file():
        return Response(content=mpath.read_bytes(), media_type=_MANIFEST_CT)

    dl = deps.get("downloader")
    if dl is not None:
        await dl.await_key(source="ollama", model_id=model_id, revision=ref)
        if mpath.is_file():
            return Response(content=mpath.read_bytes(), media_type=_MANIFEST_CT)

    # Last resort: fetch from origin (not cached, no in-flight pre-warm).
    url = f"{_OLLAMA}/v2/{name}/manifests/{ref}"
    async with deps.get("http_client").stream(
        "GET", url, headers={"Accept": _MANIFEST_CT}
    ) as up:
        body = b"".join([c async for c in up.aiter_bytes()])
        if up.status_code != 200:
            raise HTTPException(up.status_code, "upstream error")
        try:
            mpath.parent.mkdir(parents=True, exist_ok=True)
            mpath.write_bytes(body)
        except OSError:
            pass
        return Response(content=body, media_type=_MANIFEST_CT)


@router.head("/{name:path}/manifests/{ref}")
async def head_manifest(name: str, ref: str) -> Response:
    """Answer ollama's manifest existence HEAD.

    OCI clients may HEAD the manifest before GET. Without this route FastAPI
    returns 405 and the pull aborts. We mirror get_manifest's lookup but reply
    with headers only (no body): 200 when the manifest is cached or in-flight,
    otherwise proxy a HEAD to origin and relay its status.
    """
    paths = deps.get("paths")
    model_id = _model_id(name)
    mpath: Path = paths.ollama_dir(model_id, ref) / "manifest.json"

    def _ok(size: int) -> Response:
        return Response(
            status_code=200,
            headers={
                "content-type": _MANIFEST_CT,
                "content-length": str(size),
            },
        )

    if mpath.is_file():
        return _ok(mpath.stat().st_size)

    dl = deps.get("downloader")
    if dl is not None:
        await dl.await_key(source="ollama", model_id=model_id, revision=ref)
        if mpath.is_file():
            return _ok(mpath.stat().st_size)

    client = deps.get("http_client")
    if client is None:
        raise HTTPException(404, "not found")
    url = f"{_OLLAMA}/v2/{name}/manifests/{ref}"
    async with client.stream(
        "HEAD", url, headers={"Accept": _MANIFEST_CT}
    ) as up:
        if up.status_code != 200:
            raise HTTPException(up.status_code, "upstream error")
        hdrs = {"content-type": _MANIFEST_CT}
        cl = up.headers.get("content-length")
        if cl:
            hdrs["content-length"] = cl
        return Response(status_code=200, headers=hdrs)


@router.head("/{name:path}/blobs/{digest}")
async def head_blob(name: str, digest: str) -> Response:
    """Answer ollama's per-blob existence HEAD.

    `ollama pull` HEADs every blob (config + layers) before downloading to
    check existence and size. Without this route FastAPI returned 405 and the
    pull aborted with {"error":"405: "}, breaking every ollama cache-first
    deploy. We locate the blob the same way get_blob does and reply 200 with
    Content-Length + Docker-Content-Digest (no body); on a true miss we proxy a
    HEAD to origin and relay its metadata.
    """
    paths = deps.get("paths")
    model_id = _model_id(name)
    fname = digest.replace(":", "_")
    root = paths.ollama_model_dir(model_id)
    _oroot = paths.ollama_root().resolve()
    if not root.resolve().is_relative_to(_oroot):
        raise HTTPException(400, "bad path")

    def _ok(size: int) -> Response:
        return Response(
            status_code=200,
            headers={
                "content-length": str(size),
                "docker-content-digest": digest,
                "accept-ranges": "bytes",
            },
        )

    def _find() -> Path | None:
        if root.is_dir():
            for rev_dir in root.iterdir():
                cand = rev_dir / fname
                if cand.is_file():
                    return cand
        return None

    cand = _find()
    if cand is not None:
        return _ok(cand.stat().st_size)

    dl = deps.get("downloader")
    if dl is not None and root.is_dir():
        for rev_dir in root.iterdir():
            await dl.await_key(
                source="ollama", model_id=model_id, revision=rev_dir.name
            )
        cand = _find()
        if cand is not None:
            return _ok(cand.stat().st_size)

    client = deps.get("http_client")
    if client is None:
        raise HTTPException(404, "not found")
    url = f"{_OLLAMA}/v2/{name}/blobs/{digest}"
    async with client.stream("HEAD", url) as up:
        if up.status_code != 200:
            raise HTTPException(up.status_code, "upstream error")
        hdrs = {"docker-content-digest": digest, "accept-ranges": "bytes"}
        cl = up.headers.get("content-length")
        if cl:
            hdrs["content-length"] = cl
        return Response(status_code=200, headers=hdrs)


def _serve_cached_blob(path: Path, request: Request, digest: str) -> Response:
    """Serve a cached blob file with explicit HTTP Range support.

    ollama downloads a blob as N parallel byte-range parts (e.g. "16 208 MB
    part(s)"). If the server ignores Range and returns the whole file (200) for
    each part, the parts assemble into garbage and `ollama pull` aborts with
    "digest mismatch, file must be downloaded again". Starlette's FileResponse
    did NOT honor Range through our stack (returned 200 for a Range request), so
    we parse Range ourselves and stream exactly the requested slice as 206.
    """
    size = path.stat().st_size
    rng = request.headers.get("range") if request is not None else None
    if not rng or not rng.startswith("bytes="):
        return FileResponse(
            str(path),
            headers={"accept-ranges": "bytes", "docker-content-digest": digest},
        )
    spec = rng[len("bytes="):].split(",")[0].strip()
    start_s, _, end_s = spec.partition("-")
    try:
        if start_s == "":
            # suffix range: bytes=-N -> last N bytes
            n = int(end_s)
            start = max(0, size - n)
            end = size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
    except ValueError:
        return FileResponse(str(path), headers={"accept-ranges": "bytes"})
    if start >= size or start > end:
        return Response(
            status_code=416, headers={"content-range": f"bytes */{size}"},
        )
    end = min(end, size - 1)
    length = end - start + 1

    def _iter():
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        _iter(),
        status_code=206,
        headers={
            "content-range": f"bytes {start}-{end}/{size}",
            "content-length": str(length),
            "accept-ranges": "bytes",
            "docker-content-digest": digest,
        },
        media_type="application/octet-stream",
    )


@router.get("/{name:path}/blobs/{digest}")
async def get_blob(name: str, digest: str, request: Request) -> Response:
    # ollama's blob downloader resolves a "direct URL" by GETting the blob and
    # calling Go's resp.Location() on the response — UNCONDITIONALLY, on both the
    # 307 and 200 branches (registry.ollama.ai 307s to a CDN). Crucially its
    # CheckRedirect FOLLOWS same-host redirects automatically but STOPS (keeps
    # the response) on cross-host ones. So a same-host 307 gets followed to our
    # ?download=1 200, which has no Location -> "http: no Location header in
    # response" and `ollama pull` aborts. We can't hand out a real cross-host CDN
    # (that would defeat cache-first), so instead reply 200 WITH a Location
    # header: ollama doesn't follow a 200, reads Location off it, and downloads
    # the bytes from ?download=1 — still served from the CP cache, not origin.
    if not request.query_params.get("download"):
        loc = f"/v2/{name}/blobs/{digest}?download=1"
        return Response(status_code=200, headers={"location": loc})

    paths = deps.get("paths")
    model_id = _model_id(name)
    fname = digest.replace(":", "_")
    root = paths.ollama_model_dir(model_id)  # {root}/ollama/_sanitize(model_id)
    # Containment guard: model_id comes from the URL — reject any value that
    # escapes the ollama cache root (e.g. name='library/../../etc').
    _oroot = paths.ollama_root().resolve()
    if not root.resolve().is_relative_to(_oroot):
        raise HTTPException(400, "bad path")

    # Blobs are shared across tags; search this model's revision dirs.
    if root.is_dir():
        for rev_dir in root.iterdir():
            cand = rev_dir / fname
            if cand.is_file():
                return _serve_cached_blob(cand, request, digest)

    dl = deps.get("downloader")
    if dl is not None and root.is_dir():
        for rev_dir in root.iterdir():
            await dl.await_key(source="ollama", model_id=model_id, revision=rev_dir.name)
            cand = rev_dir / fname
            if cand.is_file():
                return _serve_cached_blob(cand, request, digest)

    url = f"{_OLLAMA}/v2/{name}/blobs/{digest}"
    upstream_ctx = deps.get("http_client").stream("GET", url)
    up = await upstream_ctx.__aenter__()
    if up.status_code != 200:
        await upstream_ctx.__aexit__(None, None, None)
        raise HTTPException(up.status_code, "upstream error")

    target_dir = next(iter(root.iterdir()), None) if root.is_dir() else None
    if target_dir is None:
        target_dir = root / "_blobs"
    target = target_dir / fname
    if not target.resolve().is_relative_to(_oroot):
        raise HTTPException(400, "bad path")
    tmp = target.with_suffix(target.suffix + ".part")

    async def _gen():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "wb") as fh:
                async for chunk in up.aiter_bytes():
                    fh.write(chunk)
                    yield chunk
            os.replace(tmp, target)
        except Exception:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise
        finally:
            await upstream_ctx.__aexit__(None, None, None)

    return StreamingResponse(_gen(), media_type="application/octet-stream")
