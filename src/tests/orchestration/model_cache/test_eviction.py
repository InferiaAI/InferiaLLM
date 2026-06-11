"""Tests for EvictionManager — LRU eviction with in-use guard.

TDD: tests written BEFORE eviction.py exists.
All fakes faithfully mirror the real contracts:
  - FakeRepo.lru_candidates(*, exclude_model_ids) filters out rows whose
    model_id is in the set, matching the real DB query contract.
  - FakeRepo.delete(id) records the deleted id.
  - FakePaths.total_bytes() uses a call-counter to simulate size shrinking
    after each deletion — the key to testing the loop's stop condition.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.orchestration.model_cache.eviction import EvictionManager
from services.orchestration.model_cache.paths import CachePaths


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeRepo:
    """In-memory fake that mirrors ModelCacheRepo's lru_candidates/delete contract."""

    def __init__(self, rows: list[dict]):
        # rows already ordered LRU (oldest first), exactly as the real query returns
        self._rows = rows
        self.deleted_ids: list = []
        self.lru_called = False

    async def lru_candidates(self, *, exclude_model_ids: set[str]) -> list[dict]:
        self.lru_called = True
        # Mirror the real contract: filter out rows whose model_id is in the set
        return [r for r in self._rows if r["model_id"] not in exclude_model_ids]

    async def delete(self, cache_id) -> None:
        self.deleted_ids.append(cache_id)


class FakePaths:
    """Fake CachePaths whose total_bytes() drops after a configurable number of calls.

    ``initial_bytes``: returned on the first call (and subsequent calls until
    ``drop_after_calls`` calls have been made).
    ``final_bytes``: returned once ``drop_after_calls`` calls have been made.

    This simulates the filesystem shrinking as files are deleted, so the
    eviction loop's stop condition can be exercised without a real filesystem.
    """

    def __init__(
        self,
        initial_bytes: int,
        final_bytes: int,
        drop_after_calls: int = 1,
    ):
        self._initial = initial_bytes
        self._final = final_bytes
        self._drop_after = drop_after_calls
        self._call_count = 0

    def total_bytes(self) -> int:
        self._call_count += 1
        if self._call_count > self._drop_after:
            return self._final
        return self._initial

    def hf_dir(self, model_id: str, revision: str) -> Path:
        return Path("/fake/hf") / model_id / revision

    def ollama_root(self) -> Path:
        return Path("/fake/ollama")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_under_cap_is_noop():
    """When total_bytes() <= max_bytes, lru_candidates is never called and nothing is deleted."""
    row = {"id": "old", "model_id": "a", "revision": "main", "source": "hf"}
    repo = FakeRepo([row])
    paths = FakePaths(initial_bytes=50, final_bytes=50)  # already under cap

    mgr = EvictionManager(repo=repo, paths=paths, max_bytes=100, in_use=lambda: set())
    await mgr.run_once()

    assert repo.lru_called is False
    assert repo.deleted_ids == []


@pytest.mark.asyncio
async def test_evicts_lru_until_under_cap():
    """Over cap: evicts oldest (not-in-use) row first; stops once under cap.

    Setup:
    - Two rows: "a" (oldest, id="old") and "b" (newer, id="new")
    - "b" is in-use so lru_candidates filters it out
    - total_bytes() starts over cap; drops under cap after the first deletion
    Expected: row "old" is deleted; row "new" is NOT deleted; loop stops.
    """
    row_a = {"id": "old", "model_id": "a", "revision": "main", "source": "hf"}
    row_b = {"id": "new", "model_id": "b", "revision": "main", "source": "hf"}
    # LRU order: "a" is oldest first
    repo = FakeRepo([row_a, row_b])
    # After the first deletion the size drops below the cap (200 < 300)
    paths = FakePaths(initial_bytes=500, final_bytes=200, drop_after_calls=1)

    in_use_set = {"b"}  # "b" is in use, must not be evicted
    mgr = EvictionManager(repo=repo, paths=paths, max_bytes=300, in_use=lambda: in_use_set)
    await mgr.run_once()

    assert "old" in repo.deleted_ids, "oldest LRU candidate should be evicted"
    assert "new" not in repo.deleted_ids, "in-use model must not be evicted"
    assert len(repo.deleted_ids) == 1, "should stop after one eviction (now under cap)"


@pytest.mark.asyncio
async def test_in_use_never_evicted():
    """Over cap but the only candidate is in-use → lru_candidates returns empty → nothing deleted.

    The real lru_candidates excludes in-use model_ids at the query level; the fake
    mirrors this.  Even though we are over cap we cannot evict anything.
    """
    row = {"id": "only", "model_id": "busy", "revision": "main", "source": "hf"}
    repo = FakeRepo([row])
    # Permanently over cap (total_bytes never drops)
    paths = FakePaths(initial_bytes=999, final_bytes=999)

    in_use_set = {"busy"}  # the only candidate is in use
    mgr = EvictionManager(repo=repo, paths=paths, max_bytes=100, in_use=lambda: in_use_set)
    await mgr.run_once()

    assert repo.deleted_ids == [], "in-use model must never be deleted"


def test_dir_for_hf_vs_ollama(tmp_path):
    """_dir_for returns hf_dir for source='hf' and per-model ollama_dir for source='ollama'.

    The per-model dir (contains model_id) is critical: evicting one Ollama model
    must NOT wipe all other Ollama models from disk.
    """
    paths = CachePaths(str(tmp_path))
    mgr = EvictionManager(repo=None, paths=paths, max_bytes=0, in_use=lambda: set())

    hf_row = {"id": "x", "model_id": "org/model", "revision": "abc", "source": "hf"}
    ollama_row = {"id": "y", "model_id": "llama3", "revision": "latest", "source": "ollama"}

    hf_path = mgr._dir_for(hf_row)
    assert hf_path == paths.hf_dir("org/model", "abc"), (
        f"hf path mismatch: {hf_path!r}"
    )

    ollama_path = mgr._dir_for(ollama_row)
    # Must be the PER-MODEL dir, NOT the bare ollama root
    expected_per_model = paths.ollama_dir("llama3", "latest")
    assert ollama_path == expected_per_model, (
        f"ollama path should be per-model dir {expected_per_model!r}, got {ollama_path!r}"
    )
    # Sanity: per-model dir contains model_id, bare root does not
    assert "llama3" in str(ollama_path), (
        f"per-model ollama dir should contain 'llama3', got {ollama_path!r}"
    )
    assert ollama_path != paths.ollama_root(), (
        "_dir_for must NOT return the bare ollama root (would wipe ALL ollama models)"
    )


def test_dir_for_ollama_different_models_return_different_dirs(tmp_path):
    """Two different Ollama model rows return different directories.

    This is the core invariant: evicting model A must not affect model B's blobs.
    """
    paths = CachePaths(str(tmp_path))
    mgr = EvictionManager(repo=None, paths=paths, max_bytes=0, in_use=lambda: set())

    row_a = {"id": "a", "model_id": "llama3", "revision": "latest", "source": "ollama"}
    row_b = {"id": "b", "model_id": "gemma3", "revision": "4b", "source": "ollama"}

    dir_a = mgr._dir_for(row_a)
    dir_b = mgr._dir_for(row_b)

    assert dir_a != dir_b, (
        "Different Ollama models must map to different cache dirs"
    )
    # Each dir is a subdirectory of the ollama root, not the root itself
    assert paths.ollama_root() in dir_a.parents
    assert paths.ollama_root() in dir_b.parents
