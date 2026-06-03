"""Tests for DownloadManager (TDD — written before implementation).

Fake repo stores rows in a plain dict keyed by id; it faithfully implements
the same interface as ModelCacheRepo without touching any DB.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from inferia.services.orchestration.services.model_cache.downloader import DownloadManager

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fake repo
# ---------------------------------------------------------------------------

class FakeRepo:
    """In-memory repo that faithfully mirrors ModelCacheRepo's interface."""

    def __init__(self):
        # rows keyed by id (str)
        self._rows: dict[str, dict] = {}

    def _make_row(self, **kwargs) -> dict:
        row = {
            "id": str(uuid.uuid4()),
            "source": kwargs.get("source", "hf"),
            "model_id": kwargs.get("model_id", ""),
            "revision": kwargs.get("revision", "main"),
            "engine_hint": kwargs.get("engine_hint"),
            "status": "pending",
            "bytes_total": 0,
            "bytes_done": 0,
            "error": None,
        }
        return row

    async def upsert(self, *, source, model_id, revision="main", engine_hint=None) -> dict:
        # Find existing row by natural key
        for row in self._rows.values():
            if row["source"] == source and row["model_id"] == model_id and row["revision"] == revision:
                return row
        row = self._make_row(source=source, model_id=model_id, revision=revision, engine_hint=engine_hint)
        self._rows[row["id"]] = row
        return row

    async def set_progress(self, cache_id, *, bytes_total=None, bytes_done=None, status=None) -> None:
        row = self._rows[str(cache_id)]
        if bytes_total is not None:
            row["bytes_total"] = bytes_total
        if bytes_done is not None:
            row["bytes_done"] = bytes_done
        if status is not None:
            row["status"] = status

    async def set_status(self, cache_id, status: str, error: str | None = None) -> None:
        row = self._rows[str(cache_id)]
        row["status"] = status
        row["error"] = error

    async def get(self, cache_id) -> dict | None:
        return self._rows.get(str(cache_id))


# ---------------------------------------------------------------------------
# Test 1: happy-path prewarm tracks bytes and ends cached
# ---------------------------------------------------------------------------

async def test_prewarm_marks_cached_and_tracks_bytes():
    """prewarm fetches all files and ends with status='cached', bytes_done==bytes_total==15."""
    repo = FakeRepo()

    files = [{"path": "a", "size": 10}, {"path": "b", "size": 5}]

    async def fake_fetch_list(model_id, revision):
        return files

    async def fake_fetch_file(model_id, revision, path, on_bytes):
        # Call on_bytes with the full file size
        size = next(f["size"] for f in files if f["path"] == path)
        await on_bytes(size)

    dm = DownloadManager(
        repo=repo,
        paths=None,
        fetch_list=fake_fetch_list,
        fetch_file=fake_fetch_file,
    )
    await dm.prewarm(source="hf", model_id="org/m")

    # Exactly one row was created
    assert len(repo._rows) == 1
    row = next(iter(repo._rows.values()))
    assert row["status"] == "cached"
    assert row["bytes_done"] == 15
    assert row["bytes_total"] == 15


# ---------------------------------------------------------------------------
# Test 2: prewarm failure marks error, does NOT raise
# ---------------------------------------------------------------------------

async def test_prewarm_failure_marks_error_not_raises():
    """When fetch_list raises, prewarm does NOT propagate the exception; row ends status='error'."""
    repo = FakeRepo()

    boom_msg = "network gone"

    async def bad_fetch_list(model_id, revision):
        raise RuntimeError(boom_msg)

    dm = DownloadManager(
        repo=repo,
        paths=None,
        fetch_list=bad_fetch_list,
        fetch_file=None,
    )
    # Must NOT raise
    await dm.prewarm(source="hf", model_id="org/m")

    row = next(iter(repo._rows.values()))
    assert row["status"] == "error"
    assert boom_msg in (row["error"] or "")


# ---------------------------------------------------------------------------
# Test 3: non-hf source is a no-op that ends cached
# ---------------------------------------------------------------------------

async def test_non_hf_source_is_cached_noop():
    """source='custom' (unknown source) → row ends status='cached'; fetch_list is never called.

    Note: 'ollama' is now a real source with actual download logic.
    This test covers the fallthrough path for any unrecognized source.
    """
    repo = FakeRepo()
    fetch_list_calls = []

    async def should_not_be_called(model_id, revision):
        fetch_list_calls.append((model_id, revision))
        return []

    dm = DownloadManager(
        repo=repo,
        paths=None,
        fetch_list=should_not_be_called,
        fetch_file=None,
    )
    await dm.prewarm(source="custom", model_id="llama3")

    row = next(iter(repo._rows.values()))
    assert row["status"] == "cached"
    # fetch_list was NOT called
    assert fetch_list_calls == []


# ---------------------------------------------------------------------------
# Test 4: start() deduplicates concurrent tasks by key
# ---------------------------------------------------------------------------

async def test_start_dedups_by_key():
    """Calling start() twice with the same key while the first task is still
    running returns the SAME asyncio.Task object (t1 is t2)."""
    repo = FakeRepo()

    # Gate that keeps the prewarm coroutine alive until we release it
    gate = asyncio.Event()

    files = [{"path": "model.bin", "size": 1}]

    async def gated_fetch_list(model_id, revision):
        await gate.wait()  # Block until we release the gate
        return files

    async def fast_fetch_file(model_id, revision, path, on_bytes):
        await on_bytes(1)

    dm = DownloadManager(
        repo=repo,
        paths=None,
        fetch_list=gated_fetch_list,
        fetch_file=fast_fetch_file,
    )

    # First call — starts a real asyncio.Task that is blocked at gate.wait()
    t1 = dm.start(source="hf", model_id="org/dedup", revision="main")
    # Yield to the event loop so t1 gets a chance to start and block
    await asyncio.sleep(0)

    # Second call with the SAME key — must return the identical task
    t2 = dm.start(source="hf", model_id="org/dedup", revision="main")

    assert t1 is t2, "start() must return the same task for duplicate keys"

    # Release the gate so the task can finish
    gate.set()
    await t1

    # After completion, row should be cached
    row = next(iter(repo._rows.values()))
    assert row["status"] == "cached"


# ---------------------------------------------------------------------------
# Real _hf_list (tree API) + _hf_file (status check) behavior
# ---------------------------------------------------------------------------
import json as _json
from inferia.services.orchestration.services.model_cache.paths import CachePaths


class _FakeHTTP:
    """Minimal stand-in for httpx.AsyncClient.stream()."""
    def __init__(self, *, status=200, body=b"", record=None):
        self.status, self.body, self.record = status, body, record
    def stream(self, method, url, headers=None):
        if self.record is not None:
            self.record.append(url)
        outer = self
        class _Ctx:
            async def __aenter__(self_):
                self_.status_code = outer.status
                return self_
            async def __aexit__(self_, *a):
                return False
            async def aiter_bytes(self_):
                yield outer.body
        return _Ctx()


async def test_hf_list_uses_tree_api_with_sizes():
    tree = [
        {"type": "file", "path": "config.json", "size": 651},
        {"type": "directory", "path": "sub"},
        {"type": "file", "path": "model.safetensors", "size": 0,
         "lfs": {"size": 250540281}},
    ]
    urls = []
    http = _FakeHTTP(status=200, body=_json.dumps(tree).encode(), record=urls)
    dm = DownloadManager(repo=FakeRepo(), paths=None, http_client=http, settings=None)
    files = await dm._hf_list("org/m", "main")
    assert "/tree/main?recursive=true" in urls[0]          # tree API, not siblings
    assert {"path": "config.json", "size": 651} in files
    # LFS size falls back from the lfs block:
    assert {"path": "model.safetensors", "size": 250540281} in files
    assert all(f["path"] != "sub" for f in files)          # directories skipped


async def test_hf_file_gated_raises_and_writes_nothing(tmp_path):
    http = _FakeHTTP(status=403, body=b'{"error":"gated"}')
    dm = DownloadManager(repo=FakeRepo(), paths=CachePaths(str(tmp_path)),
                         http_client=http, settings=None)
    seen = []
    with pytest.raises(RuntimeError) as ei:
        await dm._hf_file("org/m", "main", "model.safetensors", lambda n: seen.append(n))
    assert "gated" in str(ei.value).lower() or "INFERIA_HF_TOKEN" in str(ei.value)
    # no file (and no .part) was written
    target = CachePaths(str(tmp_path)).hf_dir("org/m", "main") / "model.safetensors"
    assert not target.exists()
    assert not target.with_suffix(target.suffix + ".part").exists()


async def test_hf_file_downloads_on_200(tmp_path):
    http = _FakeHTTP(status=200, body=b"WEIGHTS")
    dm = DownloadManager(repo=FakeRepo(), paths=CachePaths(str(tmp_path)),
                         http_client=http, settings=None)
    got = []
    async def on_bytes(n):
        got.append(n)
    await dm._hf_file("org/m", "main", "w.bin", on_bytes)
    target = CachePaths(str(tmp_path)).hf_dir("org/m", "main") / "w.bin"
    assert target.read_bytes() == b"WEIGHTS"
    assert sum(got) == len(b"WEIGHTS")


async def test_cancel_stops_inflight_task():
    repo = FakeRepo()
    gate = asyncio.Event()

    async def slow_fetch_list(model_id, revision):
        await gate.wait()  # block until released (simulates a long download)
        return []

    dm = DownloadManager(repo=repo, paths=None, fetch_list=slow_fetch_list,
                         fetch_file=None)
    t = dm.start(source="hf", model_id="org/m", revision="main")
    await asyncio.sleep(0.01)  # let prewarm reach the blocking fetch_list

    assert dm.cancel(source="hf", model_id="org/m", revision="main") is True
    with pytest.raises(asyncio.CancelledError):
        await t
    # No running task now → cancel is a no-op returning False.
    assert dm.cancel(source="hf", model_id="org/m", revision="main") is False
    gate.set()  # cleanup


# ---------------------------------------------------------------------------
# Ollama registry: _ollama_list, _ollama_file, and prewarm(source="ollama")
# ---------------------------------------------------------------------------

class _RouterFakeHTTP:
    """A router-style fake httpx client.

    ``routes`` is a dict mapping URL substring → ``(status, body_bytes)``.
    The FIRST matching entry wins.  Unmatched URLs return ``(404, b'')``.
    Records every requested URL in ``self.urls``.
    """

    def __init__(self, routes: dict[str, tuple[int, bytes]]):
        self._routes = routes
        self.urls: list[str] = []
        self.headers_sent: list[dict] = []

    def stream(self, method, url, headers=None):
        self.urls.append(url)
        self.headers_sent.append(dict(headers or {}))
        status, body = 404, b""
        for pattern, (s, b) in self._routes.items():
            if pattern in url:
                status, body = s, b
                break
        outer_status, outer_body = status, body

        class _Ctx:
            async def __aenter__(self_):
                self_.status_code = outer_status
                return self_

            async def __aexit__(self_, *a):
                return False

            async def aiter_bytes(self_):
                yield outer_body

        return _Ctx()


_MANIFEST_JSON = _json.dumps({
    "config": {"digest": "sha256:c", "size": 490, "mediaType": "application/vnd.oci.image.config.v1+json"},
    "layers": [
        {"digest": "sha256:m", "size": 522640096, "mediaType": "application/vnd.ollama.image.model"},
        {"digest": "sha256:t", "size": 1024, "mediaType": "application/vnd.ollama.image.template"},
    ],
}).encode()

_BLOB_BODY = b"OLLAMA_BLOB_DATA"


async def test_ollama_list_parses_manifest_correctly():
    """_ollama_list returns config + all layers as {path, size} dicts."""
    http = _RouterFakeHTTP({
        "/manifests/": (200, _MANIFEST_JSON),
    })
    dm = DownloadManager(repo=FakeRepo(), paths=None, http_client=http, settings=None)
    blobs = await dm._ollama_list("gemma3", "4b")

    # Should have config + 2 layers = 3 entries
    assert len(blobs) == 3
    assert {"path": "sha256:c", "size": 490} in blobs
    assert {"path": "sha256:m", "size": 522640096} in blobs
    assert {"path": "sha256:t", "size": 1024} in blobs

    # Total size matches spec
    total = sum(b["size"] for b in blobs)
    assert total == 490 + 522640096 + 1024


async def test_ollama_list_uses_library_prefix_for_simple_name():
    """_ollama_list adds 'library/' prefix for model names without a '/'."""
    urls = []
    http = _RouterFakeHTTP({"/manifests/": (200, _MANIFEST_JSON)})
    # Capture URL by wrapping stream
    dm = DownloadManager(repo=FakeRepo(), paths=None, http_client=http, settings=None)
    await dm._ollama_list("gemma3", "latest")
    assert any("library/gemma3" in u for u in http.urls), (
        f"Expected 'library/gemma3' in URL, got: {http.urls}"
    )


async def test_ollama_list_no_library_prefix_for_namespaced_name():
    """_ollama_list does NOT add 'library/' prefix when model_id contains '/'."""
    http = _RouterFakeHTTP({"/manifests/": (200, _MANIFEST_JSON)})
    dm = DownloadManager(repo=FakeRepo(), paths=None, http_client=http, settings=None)
    await dm._ollama_list("myorg/mymodel", "v1")
    assert any("myorg/mymodel" in u for u in http.urls)
    # Must NOT double-prefix
    assert not any("library/myorg/mymodel" in u for u in http.urls)


async def test_ollama_list_raises_on_non_200():
    """_ollama_list raises RuntimeError for any non-200 manifest response."""
    http = _RouterFakeHTTP({"/manifests/": (401, b'{"error":"unauthorized"}')})
    dm = DownloadManager(repo=FakeRepo(), paths=None, http_client=http, settings=None)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        await dm._ollama_list("gemma3", "latest")


async def test_ollama_list_accept_header_sent():
    """_ollama_list sends the required Accept header for the Ollama manifest."""
    http = _RouterFakeHTTP({"/manifests/": (200, _MANIFEST_JSON)})
    dm = DownloadManager(repo=FakeRepo(), paths=None, http_client=http, settings=None)
    await dm._ollama_list("gemma3", "latest")
    assert http.headers_sent
    accept = http.headers_sent[0].get("Accept", "")
    assert "docker.distribution.manifest" in accept


async def test_ollama_file_downloads_blob_to_per_model_dir(tmp_path):
    """_ollama_file writes the blob to ollama_dir(model_id, revision)."""
    http = _RouterFakeHTTP({"/blobs/": (200, _BLOB_BODY)})
    paths = CachePaths(str(tmp_path))
    dm = DownloadManager(repo=FakeRepo(), paths=paths, http_client=http, settings=None)
    received = []

    async def on_bytes(n):
        received.append(n)

    await dm._ollama_file("gemma3", "4b", "sha256:m", on_bytes)

    # File lands in per-model dir
    expected_dir = paths.ollama_dir("gemma3", "4b")
    blob_file = expected_dir / "sha256_m"  # ':' replaced with '_'
    assert blob_file.exists(), f"Blob file not found at {blob_file}"
    assert blob_file.read_bytes() == _BLOB_BODY
    assert sum(received) == len(_BLOB_BODY)


async def test_ollama_file_skips_if_already_present(tmp_path):
    """_ollama_file skips download and calls on_bytes with existing size when blob is already cached."""
    paths = CachePaths(str(tmp_path))
    # Pre-create the blob file
    blob_dir = paths.ollama_dir("gemma3", "4b")
    blob_dir.mkdir(parents=True, exist_ok=True)
    blob_file = blob_dir / "sha256_m"
    blob_file.write_bytes(b"ALREADY_CACHED")

    http = _RouterFakeHTTP({})  # no routes → any request would fail
    dm = DownloadManager(repo=FakeRepo(), paths=paths, http_client=http, settings=None)
    received = []

    async def on_bytes(n):
        received.append(n)

    await dm._ollama_file("gemma3", "4b", "sha256:m", on_bytes)

    # No HTTP request should have been made
    assert http.urls == [], "Should not make HTTP request when blob already exists"
    # on_bytes called with existing file size
    assert received == [len(b"ALREADY_CACHED")]


async def test_ollama_file_raises_on_non_200(tmp_path):
    """_ollama_file raises RuntimeError for non-200 blob response."""
    http = _RouterFakeHTTP({"/blobs/": (404, b"not found")})
    paths = CachePaths(str(tmp_path))
    dm = DownloadManager(repo=FakeRepo(), paths=paths, http_client=http, settings=None)

    with pytest.raises(RuntimeError, match="HTTP 404"):
        await dm._ollama_file("gemma3", "4b", "sha256:m", lambda n: None)


async def test_ollama_file_no_auth_header_sent(tmp_path):
    """_ollama_file does NOT send an Authorization header (public registry)."""
    http = _RouterFakeHTTP({"/blobs/": (200, _BLOB_BODY)})
    paths = CachePaths(str(tmp_path))
    dm = DownloadManager(repo=FakeRepo(), paths=paths, http_client=http, settings=None)

    async def noop_on_bytes(n):
        pass

    await dm._ollama_file("gemma3", "4b", "sha256:m", noop_on_bytes)
    # No Authorization header should be present
    for h in http.headers_sent:
        assert "authorization" not in {k.lower() for k in h.keys()}, (
            "Ollama blob download must not send Authorization header"
        )


async def test_prewarm_ollama_end_to_end(tmp_path):
    """prewarm(source='ollama') downloads all blobs and marks row 'cached' with correct bytes."""
    manifest = _json.dumps({
        "config": {"digest": "sha256:cfg", "size": 490},
        "layers": [{"digest": "sha256:weights", "size": 100}],
    }).encode()
    config_blob = b"CONFIG_DATA"
    weights_blob = b"WEIGHTS_DATA"

    http = _RouterFakeHTTP({
        "/manifests/": (200, manifest),
        "/blobs/sha256:cfg": (200, config_blob),
        "/blobs/sha256:weights": (200, weights_blob),
    })
    paths = CachePaths(str(tmp_path))
    repo = FakeRepo()
    dm = DownloadManager(repo=repo, paths=paths, http_client=http, settings=None)

    await dm.prewarm(source="ollama", model_id="gemma3", revision="4b")

    # Row should be cached with bytes_done == actual downloaded bytes
    assert len(repo._rows) == 1
    row = next(iter(repo._rows.values()))
    assert row["status"] == "cached"
    assert row["bytes_done"] == len(config_blob) + len(weights_blob)


async def test_prewarm_ollama_failure_marks_error(tmp_path):
    """prewarm(source='ollama') marks 'error' when manifest fetch fails; does not raise."""
    http = _RouterFakeHTTP({"/manifests/": (503, b"service unavailable")})
    paths = CachePaths(str(tmp_path))
    repo = FakeRepo()
    dm = DownloadManager(repo=repo, paths=paths, http_client=http, settings=None)

    # Must NOT raise
    await dm.prewarm(source="ollama", model_id="gemma3", revision="4b")

    row = next(iter(repo._rows.values()))
    assert row["status"] == "error"
    assert "HTTP 503" in (row["error"] or "")


async def test_prewarm_other_source_is_cached_noop():
    """source='custom' (not hf or ollama) → row ends status='cached'; no download."""
    repo = FakeRepo()
    # Use a router fake that would fail any HTTP call
    http = _RouterFakeHTTP({})
    dm = DownloadManager(repo=repo, paths=None, http_client=http, settings=None)

    await dm.prewarm(source="custom", model_id="some/model", revision="v1")

    row = next(iter(repo._rows.values()))
    assert row["status"] == "cached"
    assert http.urls == [], "No HTTP requests should be made for unknown sources"


async def test_hf_prewarm_unchanged_after_refactor():
    """prewarm(source='hf') still uses injected fetch_list/fetch_file (HF behavior unchanged)."""
    repo = FakeRepo()
    files = [{"path": "model.bin", "size": 42}]
    fetch_list_calls = []
    fetch_file_calls = []

    async def fake_list(model_id, revision):
        fetch_list_calls.append((model_id, revision))
        return files

    async def fake_file(model_id, revision, path, on_bytes):
        fetch_file_calls.append((model_id, revision, path))
        await on_bytes(42)

    dm = DownloadManager(
        repo=repo, paths=None,
        fetch_list=fake_list,
        fetch_file=fake_file,
    )
    await dm.prewarm(source="hf", model_id="org/model", revision="main")

    row = next(iter(repo._rows.values()))
    assert row["status"] == "cached"
    assert row["bytes_done"] == 42
    # Both injected functions were called exactly once
    assert fetch_list_calls == [("org/model", "main")]
    assert fetch_file_calls == [("org/model", "main", "model.bin")]
