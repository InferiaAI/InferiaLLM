"""Tests for the Ollama /v2 OCI registry mirror."""
from __future__ import annotations
import json
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from inferia.services.orchestration.services.model_cache import deps
from inferia.services.orchestration.services.model_cache.paths import CachePaths

pytestmark = pytest.mark.asyncio

_MANIFEST = {"schemaVersion": 2,
             "config": {"digest": "sha256:c", "size": 3},
             "layers": [{"digest": "sha256:m", "size": 4}]}


def _app():
    from inferia.services.orchestration.services.model_cache import mirror_ollama
    app = FastAPI(); app.include_router(mirror_ollama.router)
    return app


async def test_v2_root_probe():
    deps._reset(); deps.configure(paths=CachePaths("/tmp/x"))
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        assert (await c.get("/v2/")).status_code == 200


async def test_manifest_served_from_cache(tmp_path):
    paths = CachePaths(str(tmp_path))
    d = paths.ollama_dir("gemma3", "4b"); d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(_MANIFEST))
    deps._reset(); deps.configure(paths=paths, downloader=None, http_client=None, repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/v2/library/gemma3/manifests/4b")
    assert r.status_code == 200
    assert r.json()["config"]["digest"] == "sha256:c"


async def test_blob_served_from_cache(tmp_path):
    paths = CachePaths(str(tmp_path))
    d = paths.ollama_dir("gemma3", "4b"); d.mkdir(parents=True)
    (d / "sha256_m").write_bytes(b"BLOB")
    deps._reset(); deps.configure(paths=paths, downloader=None, http_client=None, repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/v2/library/gemma3/blobs/sha256:m")
    assert r.status_code == 200 and r.content == b"BLOB"


async def test_manifest_awaits_inflight_then_serves(tmp_path):
    paths = CachePaths(str(tmp_path))
    mpath = paths.ollama_dir("gemma3", "4b") / "manifest.json"

    class _DL:
        async def await_key(self, *, source, model_id, revision):
            mpath.parent.mkdir(parents=True, exist_ok=True)
            mpath.write_text(json.dumps(_MANIFEST))

    class _HTTP:
        def stream(self, *a, **k): raise AssertionError("origin must not be called")

    deps._reset(); deps.configure(paths=paths, downloader=_DL(), http_client=_HTTP(), repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/v2/library/gemma3/manifests/4b")
    assert r.status_code == 200


async def test_blob_awaits_inflight_then_serves(tmp_path):
    paths = CachePaths(str(tmp_path))
    blob = paths.ollama_dir("gemma3", "4b") / "sha256_m"

    class _DL:
        async def await_key(self, *, source, model_id, revision):
            blob.parent.mkdir(parents=True, exist_ok=True)
            blob.write_bytes(b"BLOB")

    class _HTTP:
        def stream(self, *a, **k): raise AssertionError("origin must not be called")

    # Pre-create the model root with an (empty) rev dir so the blob handler has
    # a rev dir to iterate + await on.
    paths.ollama_dir("gemma3", "4b").mkdir(parents=True, exist_ok=True)
    deps._reset(); deps.configure(paths=paths, downloader=_DL(), http_client=_HTTP(), repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/v2/library/gemma3/blobs/sha256:m")
    assert r.status_code == 200 and r.content == b"BLOB"


class _StreamHTTP:
    """Fake httpx client whose .stream() yields a fixed body with status 200."""
    def __init__(self, status=200, body=b"DATA"):
        self._status, self._body = status, body
    def stream(self, method, url, headers=None):
        outer = self
        class _Ctx:
            async def __aenter__(self_):
                self_.status_code = outer._status
                return self_
            async def __aexit__(self_, *a):
                return False
            async def aiter_bytes(self_):
                yield outer._body
        return _Ctx()


async def test_manifest_origin_fallback_caches_and_serves(tmp_path):
    import json
    paths = CachePaths(str(tmp_path))
    deps._reset()
    deps.configure(paths=paths, downloader=None,
                   http_client=_StreamHTTP(200, json.dumps(_MANIFEST).encode()), repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/v2/library/gemma3/manifests/4b")
    assert r.status_code == 200
    # Cached to disk for next time.
    assert (paths.ollama_dir("gemma3", "4b") / "manifest.json").is_file()


async def test_blob_origin_fallback_streams_and_caches(tmp_path):
    paths = CachePaths(str(tmp_path))
    deps._reset()
    deps.configure(paths=paths, downloader=None, http_client=_StreamHTTP(200, b"BLOBDATA"), repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/v2/library/gemma3/blobs/sha256:z")
    assert r.status_code == 200 and r.content == b"BLOBDATA"


async def test_blob_rejects_path_traversal():
    from inferia.services.orchestration.services.model_cache import mirror_ollama
    from fastapi import HTTPException
    deps._reset(); deps.configure(paths=CachePaths("/tmp/ollamatest"))
    with pytest.raises(HTTPException) as ei:
        await mirror_ollama.get_blob(name="library/../../../../etc", digest="sha256:m")
    assert ei.value.status_code == 400
