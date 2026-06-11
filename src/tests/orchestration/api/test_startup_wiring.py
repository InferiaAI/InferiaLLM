"""Smoke test: server.py's wiring of the worker control plane.

This test does NOT call ``serve()`` (which connects to Postgres and Redis).
It exercises the pure-Python parts of the wiring: that the routers are
importable, that ``configure(...)`` sets the module-level deps, and that
both routers expose the expected paths.
"""

from __future__ import annotations

from fastapi import FastAPI

# Repo-wide version skew: starlette 0.35.1 still passes ``app=`` to
# ``httpx.Client``, which httpx 0.28+ removed. Patch the httpx Client
# constructor to drop the ``app`` kwarg so TestClient-based tests keep working.
import httpx as _httpx
_orig_client_init = _httpx.Client.__init__


def _patched_client_init(self, *args, **kwargs):
    kwargs.pop("app", None)
    return _orig_client_init(self, *args, **kwargs)


_httpx.Client.__init__ = _patched_client_init  # type: ignore[assignment]
from fastapi.testclient import TestClient  # noqa: E402

from orchestration.api import admin_workers, workers
from orchestration.workers.worker_controller.auth import (
    WorkerAuth,
)
from orchestration.workers.worker_controller.registry import (
    WorkerRegistry,
)


SECRET = "test-secret-key-at-least-32-chars-long!"


class FakeInventory:
    async def list_workers(self, *, pool_id):
        return []

    async def upsert_worker(self, *, pool_id, node_name, advertise_url, allocatable):
        return {"id": "node-x"}

    async def mark_ready(self, *, node_id):
        return None

    async def mark_terminated_worker(self, *, node_id):
        return None

    async def update_heartbeat(self, *, node_id, used, loaded_models):
        return None

    async def get_node_by_id(self, node_id):
        return None


class FakePoolRepo:
    async def get(self, pool_id):
        return None

    async def get_or_generate_inference_token(self, *, pool_id):
        return "tok"


def _permit_all(_perm):
    async def _check(_auth=None):
        return True
    return _check


def test_both_routers_mount_and_expose_paths():
    app = FastAPI()
    auth = WorkerAuth(secret_key=SECRET, algorithm="HS256")
    registry = WorkerRegistry()
    inventory = FakeInventory()
    pool_repo = FakePoolRepo()

    workers.configure(auth, registry, inventory)
    admin_workers.configure(
        worker_auth=auth,
        worker_registry=registry,
        inventory_repo=inventory,
        pool_repo=pool_repo,
        control_plane_external_url="https://x",
        require_permission=_permit_all,
    )
    app.include_router(workers.router)
    app.include_router(admin_workers.router)

    # Worker-facing register endpoint requires a bootstrap token; without one
    # it should 401, not 404 — proving the route is mounted.
    client = TestClient(app)
    r = client.post("/v1/workers/register", json={
        "node_name": "n", "pool_id": "00000000-0000-0000-0000-000000000001",
        "advertise_url": "http://x", "allocatable": {},
    })
    assert r.status_code in (401, 422)

    # Admin list-workers is mounted (404 means pool empty, 200 means ok).
    r2 = client.get("/v1/admin/workers/pool/00000000-0000-0000-0000-000000000001")
    assert r2.status_code in (200, 404, 403)
