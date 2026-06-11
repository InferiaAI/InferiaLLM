"""Tests for the HF pull-through mirror router (TDD — written before implementation)."""
from __future__ import annotations

import pytest
import httpx
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport
from orchestration.models.model_cache import (
    deps,
    mirror_hf,
)
from orchestration.models.model_cache import paths as paths_mod

pytestmark = pytest.mark.asyncio


class _FakeUpstream:
    """Stands in for httpx.AsyncClient.stream — yields bytes for any GET."""

    def __init__(self, body=b"MODELBYTES", status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers or {"content-length": str(len(body))}
        self.call_count = 0
        self.last_headers: dict = {}

    def stream(self, method, url, headers=None):
        self.call_count += 1
        self.last_headers = headers or {}
        body, status, hdrs = self.body, self.status, self.headers

        class _Ctx:
            async def __aenter__(self_):
                self_.status_code = status
                self_.headers = hdrs
                return self_

            async def __aexit__(self_, *a):
                return False

            async def aiter_bytes(self_):
                yield body

        return _Ctx()


def _app(tmp_path, upstream, hf_token=""):
    deps._reset()
    deps.configure(
        paths=paths_mod.CachePaths(str(tmp_path)),
        http_client=upstream,
        settings=type("S", (), {"hf_token": hf_token})(),
    )
    app = FastAPI()
    app.include_router(mirror_hf.router)
    return app


async def test_miss_fetches_caches_and_serves(tmp_path):
    """On a cache miss, the file is fetched from upstream, written to disk, and served."""
    up = _FakeUpstream(b"HELLO")
    transport = ASGITransport(app=_app(tmp_path, up))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/hf/org/m/resolve/main/model.safetensors")
    assert r.status_code == 200 and r.content == b"HELLO"
    cached = (
        paths_mod.CachePaths(str(tmp_path)).hf_dir("org/m", "main")
        / "model.safetensors"
    )
    assert cached.read_bytes() == b"HELLO"


async def test_hit_serves_from_disk_without_upstream(tmp_path):
    """On a cache hit, the file is served from disk; upstream is never fetched."""
    cp = paths_mod.CachePaths(str(tmp_path))
    d = cp.hf_dir("org/m", "main")
    d.mkdir(parents=True)
    (d / "w").write_bytes(b"CACHED")
    up = _FakeUpstream(b"SHOULD_NOT_BE_USED")
    transport = ASGITransport(app=_app(tmp_path, up))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/hf/org/m/resolve/main/w")
    assert r.content == b"CACHED"
    # Fix 3: upstream must NOT be called on a cache hit
    assert up.call_count == 0, f"upstream was called {up.call_count} time(s) on a cache hit"


async def test_api_metadata_is_proxied(tmp_path):
    """GET /hf/api/... is proxied straight through without caching."""
    up = _FakeUpstream(
        b'{"siblings":[]}', headers={"content-type": "application/json"}
    )
    transport = ASGITransport(app=_app(tmp_path, up))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/hf/api/models/org/m")
    assert r.status_code == 200 and b"siblings" in r.content


async def test_upstream_404_returns_error_and_no_published_file(tmp_path):
    """When upstream returns 404, the endpoint returns a non-200 status and does
    NOT promote the .part temp file to the final cache path."""
    up = _FakeUpstream(b"Not Found", status=404)
    transport = ASGITransport(app=_app(tmp_path, up))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/hf/org/m/resolve/main/missing.bin")
    # Must NOT be 200
    assert r.status_code != 200, f"Expected non-200, got {r.status_code}"
    # The final file must NOT exist
    cp = paths_mod.CachePaths(str(tmp_path))
    target = cp.hf_dir("org/m", "main") / "missing.bin"
    assert not target.exists(), "Published file must not exist after upstream 404"
    # The .part temp must also NOT exist
    tmp_part = target.with_suffix(target.suffix + ".part")
    assert not tmp_part.exists(), ".part temp must be cleaned up after upstream 404"
    # Fix 2: the parent directory must NOT be created when upstream returns non-200
    assert not target.parent.exists(), "Cache dir must not be created on upstream 404"


async def test_hf_token_forwarded_to_upstream(tmp_path):
    """When hf_token is configured, the Authorization header is forwarded upstream."""
    up = _FakeUpstream(b"X")
    transport = ASGITransport(app=_app(tmp_path, up, hf_token="tok123"))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        await c.get("/hf/org/m/resolve/main/f")
    assert up.last_headers.get("authorization") == "Bearer tok123", (
        f"Expected 'Bearer tok123', got {up.last_headers.get('authorization')!r}"
    )


async def test_traversal_filename_rejected(tmp_path):
    """A filename containing '..' that escapes the base dir is rejected with HTTP 400."""
    deps._reset()
    deps.configure(
        paths=paths_mod.CachePaths(str(tmp_path)),
        http_client=_FakeUpstream(b"X"),
        settings=type("S", (), {"hf_token": ""})(),
    )
    # Call the route handler directly with a traversal filename.
    # httpx/Starlette would normalise the URL before it reaches the handler,
    # so we invoke the handler function directly to guarantee the guard fires.
    with pytest.raises(HTTPException) as ei:
        await mirror_hf.resolve_file(
            repo="org/m", rev="main", filename="../mainleak/x", request=None  # type: ignore[arg-type]
        )
    assert ei.value.status_code == 400, (
        f"Expected 400 for traversal path, got {ei.value.status_code}"
    )
    # Confirm nothing was written to disk
    import os
    assert not any(True for _ in (tmp_path / "hf").rglob("*") if os.path.isfile(_)), (
        "No file should be written for a rejected traversal path"
    )


async def test_resolve_awaits_inflight_then_serves_from_disk(tmp_path):
    from orchestration.models.model_cache import deps
    from orchestration.models.model_cache.paths import CachePaths
    from orchestration.models.model_cache import mirror_hf
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport

    paths = CachePaths(str(tmp_path))
    target = paths.hf_dir("org/m", "main") / "w.bin"

    class _DL:
        async def await_key(self, *, source, model_id, revision):
            # Simulate the pre-warm finishing: publish the file during the await.
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"WEIGHTS")

    class _HTTP:  # would raise if origin is hit
        def stream(self, *a, **k):
            raise AssertionError("origin must not be called")

    deps._reset()
    deps.configure(paths=paths, downloader=_DL(), http_client=_HTTP(), repo=None)

    app = FastAPI(); app.include_router(mirror_hf.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/hf/org/m/resolve/main/w.bin")
    assert r.status_code == 200
    assert r.content == b"WEIGHTS"


async def test_head_resolve_returns_200_with_metadata_headers(monkeypatch):
    """huggingface_hub HEADs the resolve URL for metadata before download.
    The mirror must answer 200 (not 405) with etag/x-repo-commit/content-length."""
    from orchestration.models.model_cache import deps, mirror_hf
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport

    class _HeadHTTP:
        # Mimic HF: resolve HEAD returns a 302 redirect carrying the metadata in
        # x-repo-commit / x-linked-etag / x-linked-size (the LFS shape).
        def stream(self, method, url, headers=None, follow_redirects=False):
            assert method == "HEAD"
            class _Ctx:
                async def __aenter__(self_):
                    self_.status_code = 302
                    self_.headers = {
                        "x-repo-commit": "abc123",
                        "x-linked-etag": '"deadbeef"',
                        "x-linked-size": "1503300328",
                        "content-length": "1010",  # the redirect body, NOT the file
                        "location": "https://cdn-lfs.hf.co/blob",
                    }
                    return self_
                async def __aexit__(self_, *a):
                    return False
            return _Ctx()

    deps._reset()
    deps.configure(http_client=_HeadHTTP(), settings=None, paths=None, repo=None)
    app = FastAPI(); app.include_router(mirror_hf.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.head("/hf/org/m/resolve/main/model.safetensors")
    assert r.status_code == 200
    assert r.headers.get("x-repo-commit") == "abc123"
    assert r.headers.get("etag") == '"deadbeef"'          # from x-linked-etag
    assert r.headers.get("content-length") == "1503300328"  # from x-linked-size, not 1010
    assert r.headers.get("accept-ranges") == "bytes"


async def test_head_uses_cached_size_when_hf_reports_zero(tmp_path):
    """For a non-LFS file HF's resolve HEAD is a 307 whose Content-Length is the
    redirect body and which has NO X-Linked-Size, so size extraction can land on
    0. A 0 makes huggingface_hub skip the download and write an empty file. When
    the file is cached, its real on-disk size MUST win."""
    cp = paths_mod.CachePaths(str(tmp_path))
    d = cp.hf_dir("org/m", "main"); d.mkdir(parents=True)
    (d / "tokenizer_config.json").write_bytes(b'{"k": "v"}' * 100)  # 1000 bytes
    real = (d / "tokenizer_config.json").stat().st_size

    class _ZeroSizeHTTP:
        def stream(self, method, url, headers=None, follow_redirects=False):
            class _Ctx:
                async def __aenter__(self_):
                    self_.status_code = 307
                    self_.headers = {
                        "x-repo-commit": "abc123",
                        "x-linked-etag": '"deadbeef"',
                        "content-length": "254",  # redirect body, NOT the file
                        "location": "/api/resolve-cache/x",
                    }
                    return self_
                async def __aexit__(self_, *a):
                    return False
            return _Ctx()

    deps._reset()
    deps.configure(http_client=_ZeroSizeHTTP(), settings=None, paths=cp, repo=None)
    app = FastAPI(); app.include_router(mirror_hf.router)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.head("/hf/org/m/resolve/main/tokenizer_config.json")
    assert r.status_code == 200
    assert r.headers.get("content-length") == str(real)  # cached size, not 0/254
    assert r.headers.get("x-repo-commit") == "abc123"


async def test_head_404_for_missing_uncached_file(tmp_path):
    """A genuine HF 404 with nothing cached must surface as 404 (not a fake 200
    whose GET then 404s), so optional-file probes resolve as absent."""
    cp = paths_mod.CachePaths(str(tmp_path))

    class _NotFoundHTTP:
        def stream(self, method, url, headers=None, follow_redirects=False):
            class _Ctx:
                async def __aenter__(self_):
                    self_.status_code = 404
                    self_.headers = {}
                    return self_
                async def __aexit__(self_, *a):
                    return False
            return _Ctx()

    deps._reset()
    deps.configure(http_client=_NotFoundHTTP(), settings=None, paths=cp, repo=None)
    app = FastAPI(); app.include_router(mirror_hf.router)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.head("/hf/org/m/resolve/main/special_tokens_map.json")
    assert r.status_code == 404
