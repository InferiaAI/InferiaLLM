"""Tests for ModelCacheRepo (TDD — write before implementation)."""
from __future__ import annotations

import pytest
from services.orchestration.model_cache.repo import ModelCacheRepo

pytestmark = pytest.mark.asyncio


async def test_upsert_is_idempotent(db_pool):
    """Upserting the same (source, model_id, revision) twice returns the same row."""
    repo = ModelCacheRepo(db_pool)
    a = await repo.upsert(source="hf", model_id="org/m", revision="main", engine_hint="vllm")
    b = await repo.upsert(source="hf", model_id="org/m", revision="main", engine_hint="vllm")
    assert a["id"] == b["id"]            # same row (dedup on unique key)
    assert a["status"] == "pending"


async def test_progress_and_status(db_pool):
    """set_progress updates bytes_done and status; set_status updates status alone."""
    repo = ModelCacheRepo(db_pool)
    row = await repo.upsert(source="hf", model_id="org/p", revision="main")
    await repo.set_progress(row["id"], bytes_total=100, bytes_done=40, status="downloading")
    got = await repo.get(row["id"])
    assert got["bytes_done"] == 40 and got["status"] == "downloading"
    await repo.set_status(row["id"], "cached")
    assert (await repo.get(row["id"]))["status"] == "cached"


async def test_lru_excludes_in_use(db_pool):
    """lru_candidates excludes model_ids listed in exclude_model_ids."""
    repo = ModelCacheRepo(db_pool)
    r1 = await repo.upsert(source="hf", model_id="org/old", revision="main")
    r2 = await repo.upsert(source="hf", model_id="org/new", revision="main")
    # Mark both cached so they are eligible candidates
    await repo.set_status(r1["id"], "cached")
    await repo.set_status(r2["id"], "cached")
    await repo.touch(r1["id"])
    victims = await repo.lru_candidates(exclude_model_ids={"org/new"})
    ids = [v["model_id"] for v in victims]
    assert "org/new" not in ids


async def test_lru_only_returns_cached_rows(db_pool):
    """lru_candidates must filter to status='cached'; pending rows are not candidates."""
    repo = ModelCacheRepo(db_pool)
    cached_row = await repo.upsert(source="hf", model_id="org/cached", revision="main")
    pending_row = await repo.upsert(source="hf", model_id="org/pending", revision="main")
    # Only mark the first row as cached; leave the second as 'pending'
    await repo.set_status(cached_row["id"], "cached")
    candidates = await repo.lru_candidates(exclude_model_ids=set())
    candidate_ids = [c["model_id"] for c in candidates]
    assert "org/cached" in candidate_ids
    assert "org/pending" not in candidate_ids


async def test_reconcile_orphaned_downloads(db_pool):
    """reconcile_orphaned_downloads flips only 'downloading' rows to 'error'
    (with the message) and returns the count; cached/pending rows untouched."""
    repo = ModelCacheRepo(db_pool)
    dl = await repo.upsert(source="hf", model_id="org/dl", revision="main")
    cached = await repo.upsert(source="hf", model_id="org/cc", revision="main")
    pending = await repo.upsert(source="hf", model_id="org/pp", revision="main")
    await repo.set_progress(dl["id"], status="downloading")
    await repo.set_status(cached["id"], "cached")
    # pending stays 'pending'

    n = await repo.reconcile_orphaned_downloads(message="restart")
    assert n == 1
    got_dl = await repo.get(dl["id"])
    assert got_dl["status"] == "error"
    assert got_dl["error"] == "restart"
    assert (await repo.get(cached["id"]))["status"] == "cached"
    assert (await repo.get(pending["id"]))["status"] == "pending"

    # Idempotent: a second pass finds nothing.
    assert await repo.reconcile_orphaned_downloads(message="restart") == 0


async def test_get_by_key_and_missing(db_pool):
    repo = ModelCacheRepo(db_pool)
    row = await repo.upsert(source="hf", model_id="org/x", revision="v1")
    found = await repo.get_by_key(source="hf", model_id="org/x", revision="v1")
    assert found["id"] == row["id"]
    assert await repo.get_by_key(source="hf", model_id="org/x", revision="missing") is None


async def test_delete(db_pool):
    repo = ModelCacheRepo(db_pool)
    row = await repo.upsert(source="hf", model_id="org/del", revision="main")
    await repo.delete(row["id"])
    assert await repo.get(row["id"]) is None


async def test_list_all(db_pool):
    repo = ModelCacheRepo(db_pool)
    await repo.upsert(source="hf", model_id="org/a", revision="main")
    await repo.upsert(source="ollama", model_id="llama3", revision="latest")
    rows = await repo.list_all()
    assert len(rows) == 2


async def test_touch_by_key(db_pool):
    repo = ModelCacheRepo(db_pool)
    await repo.upsert(source="hf", model_id="org/t", revision="main")
    await repo.touch_by_key(source="hf", model_id="org/t", revision="main")
    row = await repo.get_by_key(source="hf", model_id="org/t", revision="main")
    assert row is not None
