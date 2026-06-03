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
    """source='ollama' → row ends status='cached'; fetch_list is never called."""
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
    await dm.prewarm(source="ollama", model_id="llama3")

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
