"""Tests for the /v1/admin/workers/* admin router."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from inferia.services.orchestration.api import admin_workers
from inferia.services.orchestration.services.worker_controller.auth import (
    WorkerAuth,
)
from inferia.services.orchestration.services.worker_controller.registry import (
    WorkerConn,
    WorkerRegistry,
)


SECRET = "test-secret-key-at-least-32-chars-long!"
POOL_ID = "00000000-0000-0000-0000-000000000001"
NODE_ID = "11111111-2222-3333-4444-555555555555"


class FakeInventory:
    def __init__(self):
        self.terminated: list[str] = []
        self.workers: list[dict] = []
        self.allow_pool: bool = True

    async def list_workers(self, *, pool_id):
        if not self.allow_pool:
            return []
        return list(self.workers)

    async def mark_terminated_worker(self, *, node_id):
        self.terminated.append(node_id)

    async def get_node_by_id(self, node_id):
        for w in self.workers:
            if w["id"] == node_id:
                return w
        return None


class FakePoolRepo:
    def __init__(self, pool=None, token="test-pool-inference-token"):
        self.pool = pool
        self.token = token

    async def get(self, pool_id):
        return self.pool

    async def get_or_generate_inference_token(self, *, pool_id):
        return self.token


class FakeWS:
    def __init__(self):
        self.sent: list = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed = True


def fake_require_permission(perm: str):
    """RBAC checker stub for tests.

    Tests set Authorization to a comma-separated list of granted
    permissions (e.g. ``Bearer deployment:read,deployment:create``).
    Returns a callable that admin_workers._need_perm invokes with the
    Authorization header value as a single positional argument.
    """
    from fastapi import HTTPException

    def check(authorization: str | None):
        granted = (authorization or "").replace("Bearer ", "").split(",")
        granted = [p.strip() for p in granted]
        if perm not in granted:
            raise HTTPException(status_code=403, detail=f"missing {perm}")
        return True
    return check


@pytest.fixture
def app_and_deps():
    app = FastAPI()
    auth = WorkerAuth(secret_key=SECRET, algorithm="HS256")
    registry = WorkerRegistry()
    inventory = FakeInventory()
    pool_repo = FakePoolRepo(pool={
        "id": POOL_ID, "pool_name": "p", "lifecycle_state": "running",
    })

    admin_workers.configure(
        worker_auth=auth,
        worker_registry=registry,
        inventory_repo=inventory,
        pool_repo=pool_repo,
        control_plane_external_url="https://control.example.com",
        require_permission=fake_require_permission,
    )
    app.include_router(admin_workers.router)
    return app, auth, registry, inventory, pool_repo


# ---------------------------------------------------------------------------
# POST /v1/admin/workers/tokens
# ---------------------------------------------------------------------------


class TestMintToken:
    def test_happy_path(self, app_and_deps):
        app, auth, *_ = app_and_deps
        client = TestClient(app)
        r = client.post(
            "/v1/admin/workers/tokens",
            json={"pool_id": POOL_ID, "ttl_hours": 1},
            headers={"Authorization": "Bearer deployment:create"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["pool_id"] == POOL_ID
        assert data["control_plane_url"] == "https://control.example.com"
        assert data["inference_token"] == "test-pool-inference-token"
        assert data["expires_at"] > int(time.time())
        # Verify the env_snippet is multi-line with required keys.
        snippet = data["env_snippet"]
        for key in (
            "CONTROL_PLANE_URL=", "BOOTSTRAP_TOKEN=", "POOL_ID=",
            "INFERENCE_TOKEN=", "NODE_NAME=", "WORKER_ADVERTISE_URL=",
        ):
            assert key in snippet
        # Token must round-trip the auth verifier.
        claims = auth.verify_bootstrap_token(data["bootstrap_token"])
        assert claims.pool_id == POOL_ID

    def test_default_ttl_is_one_hour(self, app_and_deps):
        app, auth, *_ = app_and_deps
        client = TestClient(app)
        r = client.post(
            "/v1/admin/workers/tokens",
            json={"pool_id": POOL_ID},
            headers={"Authorization": "Bearer deployment:create"},
        )
        assert r.status_code == 200
        # exp ≈ now + 3600 (±60s tolerance for test runtime).
        delta = r.json()["expires_at"] - int(time.time())
        assert 3500 < delta < 3700

    def test_ttl_capped_at_24_hours(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.post(
            "/v1/admin/workers/tokens",
            json={"pool_id": POOL_ID, "ttl_hours": 100},
            headers={"Authorization": "Bearer deployment:create"},
        )
        assert r.status_code == 422

    def test_ttl_below_one_rejected(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.post(
            "/v1/admin/workers/tokens",
            json={"pool_id": POOL_ID, "ttl_hours": 0},
            headers={"Authorization": "Bearer deployment:create"},
        )
        assert r.status_code == 422

    def test_missing_permission_rejected(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.post(
            "/v1/admin/workers/tokens",
            json={"pool_id": POOL_ID},
            headers={"Authorization": "Bearer some_other_perm"},
        )
        assert r.status_code == 403

    def test_pool_not_found(self, app_and_deps):
        app, _auth, _reg, _inv, pool_repo = app_and_deps
        pool_repo.pool = None
        client = TestClient(app)
        r = client.post(
            "/v1/admin/workers/tokens",
            json={"pool_id": POOL_ID},
            headers={"Authorization": "Bearer deployment:create"},
        )
        assert r.status_code == 404

    def test_pool_terminated_conflict(self, app_and_deps):
        app, _auth, _reg, _inv, pool_repo = app_and_deps
        pool_repo.pool["lifecycle_state"] = "terminated"
        client = TestClient(app)
        r = client.post(
            "/v1/admin/workers/tokens",
            json={"pool_id": POOL_ID},
            headers={"Authorization": "Bearer deployment:create"},
        )
        assert r.status_code == 409


# ---------------------------------------------------------------------------
# GET /v1/admin/workers/pool/{pool_id}
# ---------------------------------------------------------------------------


class TestListWorkers:
    def test_returns_workers_with_connection_state(self, app_and_deps):
        app, _auth, registry, inventory, _pool = app_and_deps
        inventory.workers = [
            {
                "id": "node-a", "pool_id": POOL_ID, "node_name": "n1",
                "agent_kind": "worker", "state": "ready",
                "advertise_url": "http://a", "last_heartbeat": None,
                "metadata": {"used": {"cpu_pct": "5"}, "loaded_models": ["d1"]},
                "gpu_total": 1, "vcpu_total": 8, "ram_gb_total": 32,
            },
            {
                "id": "node-b", "pool_id": POOL_ID, "node_name": "n2",
                "agent_kind": "worker", "state": "provisioning",
                "advertise_url": "http://b", "last_heartbeat": None,
                "metadata": None,
                "gpu_total": 2, "vcpu_total": 16, "ram_gb_total": 64,
            },
        ]
        # node-a is connected; node-b is not.
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(registry.attach(
                "node-a", WorkerConn(ws=FakeWS(), pool_id=POOL_ID),
            ))
        finally:
            loop.close()

        client = TestClient(app)
        r = client.get(
            f"/v1/admin/workers/pool/{POOL_ID}",
            headers={"Authorization": "Bearer deployment:read"},
        )
        assert r.status_code == 200, r.text
        workers = r.json()["workers"]
        assert len(workers) == 2
        by_id = {w["node_id"]: w for w in workers}
        assert by_id["node-a"]["connected"] is True
        assert by_id["node-b"]["connected"] is False
        assert by_id["node-a"]["loaded_models"] == ["d1"]
        assert by_id["node-a"]["used"]["cpu_pct"] == "5"

    def test_empty_pool(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.get(
            f"/v1/admin/workers/pool/{POOL_ID}",
            headers={"Authorization": "Bearer deployment:read"},
        )
        assert r.status_code == 200
        assert r.json() == {"workers": []}

    def test_missing_permission(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.get(
            f"/v1/admin/workers/pool/{POOL_ID}",
            headers={"Authorization": "Bearer some_other_perm"},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /v1/admin/workers/{node_id}
# ---------------------------------------------------------------------------


class TestRevokeWorker:
    def test_marks_terminated_and_closes_ws(self, app_and_deps):
        app, _auth, registry, inventory, _pool = app_and_deps
        inventory.workers = [{"id": NODE_ID, "agent_kind": "worker"}]
        ws = FakeWS()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(registry.attach(
                NODE_ID, WorkerConn(ws=ws, pool_id=POOL_ID),
            ))
        finally:
            loop.close()

        client = TestClient(app)
        r = client.delete(
            f"/v1/admin/workers/{NODE_ID}",
            headers={"Authorization": "Bearer deployment:delete"},
        )
        assert r.status_code == 204
        assert NODE_ID in inventory.terminated
        assert ws.closed is True

    def test_idempotent_on_already_terminated(self, app_and_deps):
        app, *_, inventory, _pool = app_and_deps
        inventory.workers = [{"id": NODE_ID, "agent_kind": "worker"}]
        client = TestClient(app)
        r1 = client.delete(
            f"/v1/admin/workers/{NODE_ID}",
            headers={"Authorization": "Bearer deployment:delete"},
        )
        r2 = client.delete(
            f"/v1/admin/workers/{NODE_ID}",
            headers={"Authorization": "Bearer deployment:delete"},
        )
        assert r1.status_code == 204
        # Second call still succeeds because mark_terminated_worker is
        # idempotent at the SQL level.
        assert r2.status_code in (204, 404)  # 404 acceptable if the row check fires

    def test_unknown_node_returns_404(self, app_and_deps):
        app, *_, inventory, _pool = app_and_deps
        inventory.workers = []
        client = TestClient(app)
        r = client.delete(
            f"/v1/admin/workers/{NODE_ID}",
            headers={"Authorization": "Bearer deployment:delete"},
        )
        assert r.status_code == 404

    def test_missing_permission(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.delete(
            f"/v1/admin/workers/{NODE_ID}",
            headers={"Authorization": "Bearer some_other_perm"},
        )
        assert r.status_code == 403
