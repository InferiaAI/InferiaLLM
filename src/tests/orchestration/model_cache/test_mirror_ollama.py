"""Tests for the Ollama /v2 OCI registry mirror."""
from __future__ import annotations
import json
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from services.orchestration.model_cache import deps
from services.orchestration.model_cache.paths import CachePaths

pytestmark = pytest.mark.asyncio

_MANIFEST = {"schemaVersion": 2,
             "config": {"digest": "sha256:c", "size": 3},
             "layers": [{"digest": "sha256:m", "size": 4}]}


def _app():
    from services.orchestration.model_cache import mirror_ollama
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


async def test_blob_plain_get_returns_200_with_location(tmp_path):
    """ollama resolves the blob's direct URL by GETting it and reading
    resp.Location() — but its CheckRedirect FOLLOWS same-host redirects, so a
    307 gets followed to the 200 (no Location) -> 'http: no Location header in
    response'. We reply 200 WITH a Location (ollama doesn't follow a 200, reads
    Location off it) pointing at ?download=1 so bytes still come from the CP."""
    paths = CachePaths(str(tmp_path))
    d = paths.ollama_dir("gemma3", "4b"); d.mkdir(parents=True)
    (d / "sha256_m").write_bytes(b"BLOB")
    deps._reset(); deps.configure(paths=paths, downloader=None, http_client=None, repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/v2/library/gemma3/blobs/sha256:m")
    assert r.status_code == 200
    assert r.headers["location"] == "/v2/library/gemma3/blobs/sha256:m?download=1"


async def test_blob_served_from_cache(tmp_path):
    paths = CachePaths(str(tmp_path))
    d = paths.ollama_dir("gemma3", "4b"); d.mkdir(parents=True)
    (d / "sha256_m").write_bytes(b"BLOB")
    deps._reset(); deps.configure(paths=paths, downloader=None, http_client=None, repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/v2/library/gemma3/blobs/sha256:m?download=1")
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
        r = await c.get("/v2/library/gemma3/blobs/sha256:m?download=1")
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
        r = await c.get("/v2/library/gemma3/blobs/sha256:z?download=1")
    assert r.status_code == 200 and r.content == b"BLOBDATA"


async def test_blob_path_traversal_is_contained(tmp_path):
    """A traversal-y registry name cannot escape the ollama cache root: the
    model_id is sanitised into a single safe segment (matching the downloader's
    write path), so the resolved model dir stays under ollama_root."""
    paths = CachePaths(str(tmp_path))
    root = paths.ollama_model_dir("../../../../etc")  # what get_blob computes
    oroot = paths.ollama_root().resolve()
    assert root.resolve().is_relative_to(oroot)
    assert not (root / "sha256_m").exists()


# ---------------------------------------------------------------------------
# HEAD handlers — ollama's registry client HEADs each blob (and the manifest)
# before GET to check existence/size. Without these routes FastAPI returns 405
# Method Not Allowed and `ollama pull` aborts with {"error":"405: "}, so the
# whole /v2 cache-first path for ollama deploys never works (seen live: the
# gemma3:4b deploy FAILED at load on `HEAD /v2/library/gemma3/blobs/<digest>`).
# ---------------------------------------------------------------------------

from services.orchestration.model_cache.mirror_ollama import (  # noqa: E402
    _MANIFEST_CT,
)


async def test_blob_head_cached_returns_size(tmp_path):
    paths = CachePaths(str(tmp_path))
    d = paths.ollama_dir("gemma3", "4b"); d.mkdir(parents=True)
    (d / "sha256_m").write_bytes(b"BLOBBYTES")  # 9 bytes
    deps._reset(); deps.configure(paths=paths, downloader=None, http_client=None, repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.head("/v2/library/gemma3/blobs/sha256:m")
    assert r.status_code == 200
    assert r.headers["content-length"] == "9"
    assert r.headers["docker-content-digest"] == "sha256:m"
    assert r.headers.get("accept-ranges") == "bytes"
    assert r.content == b""  # HEAD has no body


async def test_blob_head_awaits_inflight_then_200(tmp_path):
    paths = CachePaths(str(tmp_path))
    # The rev dir exists (ollama fetched the manifest first); only the blob is
    # still being written by the in-flight pre-warm. This mirrors get_blob's
    # await semantics, which iterate the model's existing revision dirs.
    rev_dir = paths.ollama_dir("gemma3", "4b"); rev_dir.mkdir(parents=True)
    blob = rev_dir / "sha256_m"

    class _DL:
        async def await_key(self, *, source, model_id, revision):
            blob.write_bytes(b"LATE")  # 4 bytes, published during the await

    class _HTTP:
        def stream(self, *a, **k): raise AssertionError("origin must not be called")

    deps._reset(); deps.configure(paths=paths, downloader=_DL(), http_client=_HTTP(), repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.head("/v2/library/gemma3/blobs/sha256:m")
    assert r.status_code == 200
    assert r.headers["content-length"] == "4"


async def test_blob_head_origin_fallback_relays_metadata(tmp_path):
    """Cache miss with no in-flight pre-warm: proxy a HEAD to origin and relay
    the status + content-length so ollama can size the download."""
    class _HeadHTTP:
        def stream(self, method, url, headers=None):
            assert method == "HEAD"
            class _Ctx:
                async def __aenter__(self_):
                    self_.status_code = 200
                    self_.headers = {"content-length": "12345",
                                     "docker-content-digest": "sha256:z"}
                    return self_
                async def __aexit__(self_, *a):
                    return False
            return _Ctx()

    paths = CachePaths(str(tmp_path))
    deps._reset(); deps.configure(paths=paths, downloader=None, http_client=_HeadHTTP(), repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.head("/v2/library/gemma3/blobs/sha256:z")
    assert r.status_code == 200
    assert r.headers["content-length"] == "12345"


async def test_manifest_head_cached_returns_200(tmp_path):
    import json
    paths = CachePaths(str(tmp_path))
    d = paths.ollama_dir("gemma3", "4b"); d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(_MANIFEST))
    deps._reset(); deps.configure(paths=paths, downloader=None, http_client=None, repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.head("/v2/library/gemma3/manifests/4b")
    assert r.status_code == 200
    assert r.headers["content-type"] == _MANIFEST_CT
    assert r.content == b""


async def test_manifest_head_origin_fallback(tmp_path):
    class _HeadHTTP:
        def stream(self, method, url, headers=None):
            assert method == "HEAD"
            class _Ctx:
                async def __aenter__(self_):
                    self_.status_code = 200
                    self_.headers = {"content-length": "77"}
                    return self_
                async def __aexit__(self_, *a):
                    return False
            return _Ctx()

    paths = CachePaths(str(tmp_path))
    deps._reset(); deps.configure(paths=paths, downloader=None, http_client=_HeadHTTP(), repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.head("/v2/library/gemma3/manifests/4b")
    assert r.status_code == 200
    assert r.headers["content-length"] == "77"


async def test_manifest_head_awaits_inflight_then_200(tmp_path):
    import json
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
        r = await c.head("/v2/library/gemma3/manifests/4b")
    assert r.status_code == 200
    assert r.headers["content-type"] == _MANIFEST_CT


async def test_manifest_head_origin_non_200_propagates(tmp_path):
    class _HeadHTTP:
        def stream(self, method, url, headers=None):
            class _Ctx:
                async def __aenter__(self_):
                    self_.status_code = 404
                    self_.headers = {}
                    return self_
                async def __aexit__(self_, *a):
                    return False
            return _Ctx()

    paths = CachePaths(str(tmp_path))
    deps._reset(); deps.configure(paths=paths, downloader=None, http_client=_HeadHTTP(), repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.head("/v2/library/gemma3/manifests/4b")
    assert r.status_code == 404


async def test_manifest_head_404_when_no_client(tmp_path):
    paths = CachePaths(str(tmp_path))
    deps._reset(); deps.configure(paths=paths, downloader=None, http_client=None, repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.head("/v2/library/gemma3/manifests/4b")
    assert r.status_code == 404


async def test_blob_head_404_when_no_client(tmp_path):
    paths = CachePaths(str(tmp_path))
    deps._reset(); deps.configure(paths=paths, downloader=None, http_client=None, repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.head("/v2/library/gemma3/blobs/sha256:gone")
    assert r.status_code == 404


async def test_blob_head_origin_non_200_propagates(tmp_path):
    class _HeadHTTP:
        def stream(self, method, url, headers=None):
            class _Ctx:
                async def __aenter__(self_):
                    self_.status_code = 404
                    self_.headers = {}
                    return self_
                async def __aexit__(self_, *a):
                    return False
            return _Ctx()

    paths = CachePaths(str(tmp_path))
    deps._reset(); deps.configure(paths=paths, downloader=None, http_client=_HeadHTTP(), repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.head("/v2/library/gemma3/blobs/sha256:missing")
    assert r.status_code == 404


async def test_blob_head_origin_no_content_length(tmp_path):
    """Origin HEAD 200 without content-length still returns 200 (size optional)."""
    class _HeadHTTP:
        def stream(self, method, url, headers=None):
            class _Ctx:
                async def __aenter__(self_):
                    self_.status_code = 200
                    self_.headers = {}
                    return self_
                async def __aexit__(self_, *a):
                    return False
            return _Ctx()

    paths = CachePaths(str(tmp_path))
    deps._reset(); deps.configure(paths=paths, downloader=None, http_client=_HeadHTTP(), repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.head("/v2/library/gemma3/blobs/sha256:nolen")
    assert r.status_code == 200
    assert r.headers["docker-content-digest"] == "sha256:nolen"
    # We did not forward an upstream content-length; Starlette only adds its own
    # content-length: 0 for the empty HEAD body, never a real (wrong) size.
    assert r.headers.get("content-length") in (None, "0")


async def test_blob_download_honors_range(tmp_path):
    """ollama downloads a blob as parallel byte-range parts; ?download=1 MUST
    return 206 with exactly the requested slice, else parts assemble into a
    digest mismatch ('file must be downloaded again')."""
    paths = CachePaths(str(tmp_path))
    d = paths.ollama_dir("gemma3", "4b"); d.mkdir(parents=True)
    (d / "sha256_m").write_bytes(b"0123456789ABCDEF")  # 16 bytes
    deps._reset(); deps.configure(paths=paths, downloader=None, http_client=None, repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/v2/library/gemma3/blobs/sha256:m?download=1",
                        headers={"Range": "bytes=4-7"})
    assert r.status_code == 206
    assert r.content == b"4567"
    assert r.headers["content-range"] == "bytes 4-7/16"
    assert r.headers["content-length"] == "4"
    assert r.headers["accept-ranges"] == "bytes"


async def test_blob_download_full_when_no_range(tmp_path):
    paths = CachePaths(str(tmp_path))
    d = paths.ollama_dir("gemma3", "4b"); d.mkdir(parents=True)
    (d / "sha256_m").write_bytes(b"0123456789ABCDEF")
    deps._reset(); deps.configure(paths=paths, downloader=None, http_client=None, repo=None)
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/v2/library/gemma3/blobs/sha256:m?download=1")
    assert r.status_code == 200
    assert r.content == b"0123456789ABCDEF"
