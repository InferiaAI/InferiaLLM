"""Tests for model_cache management API router (Phase 6).

All five tests use httpx AsyncClient / ASGITransport with a fake repo /
downloader / eviction injected via deps.configure.  No database is involved.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from services.orchestration.model_cache import deps

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRepo:
    def __init__(self):
        self._rows: dict[str, dict] = {}
        self.deleted: list[str] = []

    def seed(self, row: dict) -> None:
        self._rows[str(row["id"])] = row

    async def list_all(self) -> list[dict]:
        return list(self._rows.values())

    async def get(self, cache_id: str) -> dict | None:
        return self._rows.get(str(cache_id))

    async def delete(self, cache_id: str) -> None:
        self.deleted.append(str(cache_id))
        self._rows.pop(str(cache_id), None)


class _FakeDownloader:
    def __init__(self):
        self.calls: list[dict] = []
        self.cancelled: list[dict] = []

    def start(self, *, source, model_id, revision="main", engine_hint=None):
        self.calls.append(
            {"source": source, "model_id": model_id, "revision": revision, "engine_hint": engine_hint}
        )

    def cancel(self, *, source, model_id, revision="main"):
        self.cancelled.append(
            {"source": source, "model_id": model_id, "revision": revision}
        )
        return True


class _FakeEviction:
    """Fake eviction manager; _dir_for returns a path-like whose rmtree is a no-op."""

    def _dir_for(self, row: dict):
        class _FakePath:
            def __str__(self_inner):
                return f"/fake/cache/{row.get('model_id', 'unknown')}"

            def __fspath__(self_inner):
                return str(self_inner)

        return _FakePath()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(fake_repo, fake_downloader, fake_eviction=None):
    deps._reset()
    deps.configure(repo=fake_repo, downloader=fake_downloader, eviction=fake_eviction)
    from services.orchestration.model_cache.api import router
    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_add_enqueues_download():
    """POST /v1/models → 202; fake downloader records start() call."""
    repo = _FakeRepo()
    dl = _FakeDownloader()
    app = _make_app(repo, dl)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/models",
            json={"source": "hf", "model_id": "org/m", "engine": "vllm"},
        )

    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "downloading"
    assert data["model_id"] == "org/m"

    assert len(dl.calls) == 1
    call = dl.calls[0]
    assert call["model_id"] == "org/m"
    assert call["source"] == "hf"
    assert call["engine_hint"] == "vllm"


async def test_list_returns_repo_rows():
    """GET /v1/models → returns all seeded rows."""
    repo = _FakeRepo()
    repo.seed({"id": "aaaa-1111", "model_id": "org/model1", "status": "cached"})
    repo.seed({"id": "bbbb-2222", "model_id": "org/model2", "status": "downloading"})
    dl = _FakeDownloader()
    app = _make_app(repo, dl)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/models")

    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert len(data["models"]) == 2


async def test_progress_404_when_missing():
    """GET /v1/models/<unknown>/progress → 404."""
    repo = _FakeRepo()
    dl = _FakeDownloader()
    app = _make_app(repo, dl)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/models/does-not-exist/progress")

    assert resp.status_code == 404


async def test_progress_returns_fields():
    """GET /v1/models/<id>/progress → returns status/bytes_total/bytes_done/error."""
    repo = _FakeRepo()
    repo.seed({
        "id": "cccc-3333",
        "model_id": "org/big-model",
        "status": "downloading",
        "bytes_total": 1000,
        "bytes_done": 400,
        "error": None,
    })
    dl = _FakeDownloader()
    app = _make_app(repo, dl)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/models/cccc-3333/progress")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "downloading"
    assert data["bytes_total"] == 1000
    assert data["bytes_done"] == 400
    assert data["error"] is None


async def test_delete_removes_row():
    """DELETE /v1/models/<id> → 204 and repo.delete called; unknown id → 404."""
    repo = _FakeRepo()
    repo.seed({
        "id": "dddd-4444",
        "model_id": "org/removable",
        "status": "cached",
        "source": "hf",
    })
    dl = _FakeDownloader()
    eviction = _FakeEviction()
    app = _make_app(repo, dl, eviction)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Successful delete
        resp = await client.delete("/v1/models/dddd-4444")
        assert resp.status_code == 204

        # Confirm repo.delete was called with the correct id
        assert "dddd-4444" in repo.deleted
        # Delete must also cancel any in-flight download for that model.
        assert any(c["model_id"] == "org/removable" for c in dl.cancelled)

        # Unknown id → 404
        resp = await client.delete("/v1/models/no-such-id")
        assert resp.status_code == 404
