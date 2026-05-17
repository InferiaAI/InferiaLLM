"""End-to-end tests for the /v1/workers/* router."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from inferia.services.orchestration.api import workers
from inferia.services.orchestration.services.worker_controller.auth import (
    WorkerAuth,
)
from inferia.services.orchestration.services.worker_controller.registry import (
    WorkerRegistry,
)
from inferia.services.orchestration.services.worker_controller.protocol import (
    Envelope,
)


SECRET = "test-secret-key-at-least-32-chars-long!"
POOL_ID = "00000000-0000-0000-0000-000000000001"


class FakeInventory:
    """In-memory stand-in for InventoryRepository."""

    def __init__(self):
        self.nodes: dict[tuple[str, str], dict] = {}
        self.heartbeats: list[dict] = []
        self.marked_ready: list[str] = []
        self.duplicate_kind: bool = False  # toggles to force a conflict

    async def upsert_worker(self, *, pool_id, node_name, advertise_url, allocatable):
        if self.duplicate_kind:
            raise workers.DuplicateNodeError(
                f"{pool_id}/{node_name} taken by a non-worker node"
            )
        key = (pool_id, node_name)
        if key in self.nodes:
            row = self.nodes[key]
        else:
            row = {
                "id": f"node-{node_name}",
                "pool_id": pool_id,
                "node_name": node_name,
                "kind": "worker",
                "state": "provisioning",
                "advertise_url": advertise_url,
                "allocatable": allocatable,
            }
            self.nodes[key] = row
        return row

    async def mark_ready(self, *, node_id):
        self.marked_ready.append(node_id)

    async def mark_ready_worker(self, *, node_id):
        self.marked_ready.append(node_id)

    async def get_node_by_id(self, node_id):
        # No revoked-node check in these tests; return a non-terminated row
        # for any id that's already been "registered" (i.e. attempts to
        # mark_ready_worker). Otherwise return None to mirror real behaviour.
        return {"id": node_id, "state": "ready"}

    async def update_heartbeat(self, *, node_id, used, loaded_models):
        self.heartbeats.append({"node_id": node_id, "used": used, "loaded_models": loaded_models})

    async def update_heartbeat_with_telemetry(self, *, node_id, used, loaded_models):
        self.heartbeats.append({"node_id": node_id, "used": used, "loaded_models": loaded_models})


@pytest.fixture
def app_and_deps():
    app = FastAPI()
    auth = WorkerAuth(secret_key=SECRET, algorithm="HS256")
    registry = WorkerRegistry()
    inventory = FakeInventory()
    workers.configure(auth, registry, inventory)
    app.include_router(workers.router)
    return app, auth, registry, inventory


def test_register_happy_path(app_and_deps):
    app, auth, _registry, inventory = app_and_deps
    token = auth.mint_bootstrap_token(pool_id=POOL_ID)
    client = TestClient(app)
    r = client.post(
        "/v1/workers/register",
        json={
            "node_name": "n1",
            "pool_id": POOL_ID,
            "advertise_url": "https://w:8080",
            "allocatable": {"cpu": "16"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["node_id"] == "node-n1"
    assert data["worker_jwt"]
    # Token round-trip with verifier.
    claims = auth.verify_worker_token(data["worker_jwt"])
    assert claims.sub == "node-n1"
    assert claims.pool_id == POOL_ID


def test_register_missing_token(app_and_deps):
    app, _auth, _reg, _inv = app_and_deps
    client = TestClient(app)
    r = client.post("/v1/workers/register", json={
        "node_name": "n", "pool_id": POOL_ID, "advertise_url": "http://x",
    })
    assert r.status_code == 401


def test_register_invalid_token(app_and_deps):
    app, _auth, _reg, _inv = app_and_deps
    client = TestClient(app)
    r = client.post(
        "/v1/workers/register",
        json={"node_name": "n", "pool_id": POOL_ID, "advertise_url": "http://x"},
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert r.status_code == 401


def test_register_user_token_rejected(app_and_deps):
    """A user-style token (not scope=worker:bootstrap) must not register."""
    app, auth, _reg, _inv = app_and_deps
    user_token = auth.mint_worker_token(node_id="n", pool_id=POOL_ID)
    client = TestClient(app)
    r = client.post(
        "/v1/workers/register",
        json={"node_name": "n", "pool_id": POOL_ID, "advertise_url": "http://x"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert r.status_code == 401


def test_register_pool_mismatch(app_and_deps):
    app, auth, _reg, _inv = app_and_deps
    boot = auth.mint_bootstrap_token(pool_id=POOL_ID)
    client = TestClient(app)
    r = client.post(
        "/v1/workers/register",
        json={
            "node_name": "n", "pool_id": "11111111-2222-3333-4444-555555555555",
            "advertise_url": "http://x",
        },
        headers={"Authorization": f"Bearer {boot}"},
    )
    assert r.status_code == 403


def test_register_duplicate_node_kind_conflict(app_and_deps):
    app, auth, _reg, inventory = app_and_deps
    inventory.duplicate_kind = True
    boot = auth.mint_bootstrap_token(pool_id=POOL_ID)
    client = TestClient(app)
    r = client.post(
        "/v1/workers/register",
        json={"node_name": "n", "pool_id": POOL_ID, "advertise_url": "http://x"},
        headers={"Authorization": f"Bearer {boot}"},
    )
    assert r.status_code == 409


def test_register_idempotent_reissues_token(app_and_deps):
    """Calling /register twice for the same (pool, node_name) yields the same
    node_id (re-registration after token loss path)."""
    app, auth, _reg, _inv = app_and_deps
    boot = auth.mint_bootstrap_token(pool_id=POOL_ID)
    client = TestClient(app)
    r1 = client.post(
        "/v1/workers/register",
        json={"node_name": "n", "pool_id": POOL_ID, "advertise_url": "http://x"},
        headers={"Authorization": f"Bearer {boot}"},
    )
    r2 = client.post(
        "/v1/workers/register",
        json={"node_name": "n", "pool_id": POOL_ID, "advertise_url": "http://x"},
        headers={"Authorization": f"Bearer {boot}"},
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["node_id"] == r2.json()["node_id"]


# ---------------------------------------------------------------------------
# WebSocket channel
# ---------------------------------------------------------------------------


def test_channel_invalid_token_closes(app_and_deps):
    app, _auth, _reg, _inv = app_and_deps
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/v1/workers/channel",
                                      headers={"Authorization": "Bearer nope"}):
            pass


def test_channel_missing_token_closes(app_and_deps):
    app, _auth, _reg, _inv = app_and_deps
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/v1/workers/channel"):
            pass


def test_channel_hello_then_heartbeat(app_and_deps):
    app, auth, _reg, inventory = app_and_deps
    token = auth.mint_worker_token(node_id="node-n1", pool_id=POOL_ID)
    client = TestClient(app)
    with client.websocket_connect(
        "/v1/workers/channel",
        headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "Hello"
        ws.send_json({
            "type": "Heartbeat",
            "id": "hb-1",
            "body": {"used": {"cpu_pct": "10"}, "loaded_models": ["dep-1"]},
        })
        # Give the server a moment to process the message.
        import time as _t
        _t.sleep(0.1)

    # After the context exits, the worker should have been marked ready and
    # at least one heartbeat recorded.
    assert "node-n1" in inventory.marked_ready
    assert any(h["node_id"] == "node-n1" for h in inventory.heartbeats)


def test_channel_command_result_routed_to_registry(app_and_deps):
    app, auth, registry, _inv = app_and_deps
    token = auth.mint_worker_token(node_id="node-n2", pool_id=POOL_ID)
    client = TestClient(app)

    with client.websocket_connect(
        "/v1/workers/channel",
        headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        _hello = ws.receive_json()
        # Park a future on the registry then send a matching CommandResult.
        loop = asyncio.new_event_loop()
        fut = loop.run_until_complete(_park(registry, "cmd-x"))
        ws.send_json({
            "type": "CommandResult",
            "id": "ws-id",
            "body": {"in_reply_to": "cmd-x", "status": "ok"},
        })
        import time as _t
        _t.sleep(0.1)
        result = loop.run_until_complete(_await(fut))
        loop.close()
        assert result.status == "ok"


async def _park(registry: WorkerRegistry, envelope_id: str):
    return registry.expect_command_result(envelope_id, timeout=2.0)


async def _await(fut):
    return await fut
