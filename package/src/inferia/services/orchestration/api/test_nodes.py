"""Tests for /v1/nodes/* router."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from inferia.services.orchestration.api import nodes as nodes_api
from inferia.services.orchestration.repositories.inventory_repo import (
    NodeNotFoundError,
    NodeTerminatedError,
    LabelConflictError,
)


ORG = "69ff5234-a4ea-4c88-adf0-d5702508f7ef"
POOL = "942d7675-5633-4a72-a5e7-defbf4866ab5"
NODE = "11111111-2222-3333-4444-555555555555"


class FakeInventory:
    def __init__(self):
        self.nodes: dict[str, dict] = {}

    async def list_nodes(self, *, org_id, selector=None):
        out = []
        for n in self.nodes.values():
            if selector and not all(
                n.get("labels", {}).get(k) == v for k, v in selector.items()
            ):
                continue
            if n.get("state") == "terminated":
                continue
            out.append(n)
        return out

    async def get_node(self, *, node_id):
        return self.nodes.get(node_id)

    async def set_labels(self, *, node_id, add, remove):
        node = self.nodes.get(node_id)
        if not node:
            raise NodeNotFoundError(node_id)
        if node.get("state") == "terminated":
            raise NodeTerminatedError(node_id)
        if set(add.keys()) & set(remove):
            raise LabelConflictError("overlap")
        new = dict(node.get("labels", {}))
        new.update(add)
        for k in remove:
            new.pop(k, None)
        node["labels"] = new
        return node

    async def soft_delete_node(self, *, node_id):
        if node_id in self.nodes:
            self.nodes[node_id]["state"] = "terminated"

    async def get_deployments_for_node(self, node_id):
        return []  # default: no deployments

    async def upsert_worker(self, *, pool_id, node_name, advertise_url, allocatable):
        nid = NODE
        self.nodes[nid] = {
            "id": nid, "pool_id": pool_id, "node_name": node_name,
            "agent_kind": "worker", "state": "provisioning",
            "advertise_url": advertise_url, "labels": {},
        }
        return self.nodes[nid]


class FakePoolRepo:
    async def ensure_default_pool(self, *, org_id):
        return POOL


class FakeAdapter:
    def __init__(self, raises=None):
        self.raises = raises
        self.calls: list[Any] = []

    async def provision_single_node(self, *, pool_id, org_id, spec):
        self.calls.append(spec)
        if self.raises:
            raise self.raises
        return {
            "id": "new-nosana-node", "pool_id": pool_id, "node_name": spec.get("node_name", "n-1"),
            "agent_kind": "nosana", "state": "provisioning",
            "provider": "nosana", "provider_instance_id": "pi-1",
            "advertise_url": None, "expose_url": "https://node:1234",
            "labels": spec.get("labels", {}),
        }


class FakeWorkerAuth:
    def mint_bootstrap_token(self, *, pool_id, ttl_seconds=None):
        return "fake-bootstrap-token"


def fake_require_permission(perm: str):
    from fastapi import Header, HTTPException

    async def dep(authorization: str | None = Header(default=None)):
        granted = (authorization or "").replace("Bearer ", "").split(",")
        granted = [p.strip() for p in granted]
        if perm not in granted:
            raise HTTPException(status_code=403, detail=f"missing {perm}")
        return True

    return dep


def _user_ctx_header():
    return {"Authorization": "Bearer deployment:list,deployment:create,deployment:update,deployment:delete",
            "X-User-ID": "user-1", "X-Organization-ID": ORG}


@pytest.fixture
def app_and_deps():
    app = FastAPI()
    inventory = FakeInventory()
    pool_repo = FakePoolRepo()
    nosana = FakeAdapter()
    akash = FakeAdapter()
    nodes_api.configure(
        inventory_repo=inventory,
        pool_repo=pool_repo,
        worker_auth=FakeWorkerAuth(),
        control_plane_external_url="https://control.example.com",
        adapters={"nosana": nosana, "akash": akash},
        require_permission=fake_require_permission,
    )
    app.include_router(nodes_api.router)
    return app, inventory, nosana, akash


# ---------------------------------------------------------------------------
# GET /v1/nodes
# ---------------------------------------------------------------------------


class TestListNodes:
    def test_empty(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.get("/v1/nodes", headers=_user_ctx_header())
        assert r.status_code == 200
        assert r.json() == {"nodes": []}

    def test_returns_nodes(self, app_and_deps):
        app, inventory, *_ = app_and_deps
        inventory.nodes["a"] = {
            "id": "a", "pool_id": POOL, "node_name": "host-a",
            "agent_kind": "worker", "state": "ready",
            "advertise_url": "http://a", "labels": {"gpu": "h100"},
            "gpu_total": 1, "vcpu_total": 8, "ram_gb_total": 32,
            "last_heartbeat": None, "metadata": None, "provider": "on_prem",
        }
        client = TestClient(app)
        r = client.get("/v1/nodes", headers=_user_ctx_header())
        assert r.status_code == 200
        rows = r.json()["nodes"]
        assert len(rows) == 1
        assert rows[0]["labels"] == {"gpu": "h100"}

    def test_filters_by_selector(self, app_and_deps):
        app, inventory, *_ = app_and_deps
        inventory.nodes["a"] = {
            "id": "a", "pool_id": POOL, "agent_kind": "worker", "state": "ready",
            "labels": {"gpu": "h100"}, "gpu_total": 1, "vcpu_total": 8,
            "ram_gb_total": 32, "metadata": None, "provider": "on_prem",
            "last_heartbeat": None, "advertise_url": None,
        }
        inventory.nodes["b"] = {
            "id": "b", "pool_id": POOL, "agent_kind": "worker", "state": "ready",
            "labels": {"gpu": "a100"}, "gpu_total": 1, "vcpu_total": 8,
            "ram_gb_total": 32, "metadata": None, "provider": "on_prem",
            "last_heartbeat": None, "advertise_url": None,
        }
        client = TestClient(app)
        r = client.get("/v1/nodes?labels=gpu=h100", headers=_user_ctx_header())
        assert r.status_code == 200
        rows = r.json()["nodes"]
        assert {n["id"] for n in rows} == {"a"}

    def test_malformed_selector(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.get("/v1/nodes?labels=foo", headers=_user_ctx_header())
        assert r.status_code == 422

    def test_unauthorized(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.get("/v1/nodes")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /v1/nodes/{id}
# ---------------------------------------------------------------------------


class TestGetNode:
    def test_happy(self, app_and_deps):
        app, inventory, *_ = app_and_deps
        inventory.nodes["a"] = {
            "id": "a", "pool_id": POOL, "agent_kind": "worker", "state": "ready",
            "labels": {"gpu": "h100"}, "gpu_total": 1, "vcpu_total": 8,
            "ram_gb_total": 32, "metadata": None, "provider": "on_prem",
            "last_heartbeat": None, "advertise_url": None,
        }
        client = TestClient(app)
        r = client.get("/v1/nodes/a", headers=_user_ctx_header())
        assert r.status_code == 200
        assert r.json()["id"] == "a"

    def test_not_found(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.get("/v1/nodes/missing", headers=_user_ctx_header())
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /v1/nodes/{id}/labels
# ---------------------------------------------------------------------------


class TestPatchLabels:
    def test_add(self, app_and_deps):
        app, inventory, *_ = app_and_deps
        inventory.nodes[NODE] = {"id": NODE, "state": "ready", "labels": {}}
        client = TestClient(app)
        r = client.patch(
            f"/v1/nodes/{NODE}/labels",
            json={"add": {"env": "prod"}, "remove": []},
            headers=_user_ctx_header(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["labels"] == {"env": "prod"}

    def test_terminated(self, app_and_deps):
        app, inventory, *_ = app_and_deps
        inventory.nodes[NODE] = {"id": NODE, "state": "terminated", "labels": {}}
        client = TestClient(app)
        r = client.patch(
            f"/v1/nodes/{NODE}/labels",
            json={"add": {"env": "prod"}, "remove": []},
            headers=_user_ctx_header(),
        )
        assert r.status_code == 409

    def test_not_found(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.patch(
            f"/v1/nodes/{NODE}/labels",
            json={"add": {"env": "prod"}, "remove": []},
            headers=_user_ctx_header(),
        )
        assert r.status_code == 404

    def test_overlap(self, app_and_deps):
        app, inventory, *_ = app_and_deps
        inventory.nodes[NODE] = {"id": NODE, "state": "ready", "labels": {}}
        client = TestClient(app)
        r = client.patch(
            f"/v1/nodes/{NODE}/labels",
            json={"add": {"env": "prod"}, "remove": ["env"]},
            headers=_user_ctx_header(),
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /v1/nodes/{id}
# ---------------------------------------------------------------------------


class TestDeleteNode:
    def test_happy(self, app_and_deps):
        app, inventory, *_ = app_and_deps
        inventory.nodes[NODE] = {"id": NODE, "state": "ready", "labels": {}}
        client = TestClient(app)
        r = client.delete(f"/v1/nodes/{NODE}", headers=_user_ctx_header())
        assert r.status_code == 204
        assert inventory.nodes[NODE]["state"] == "terminated"

    def test_not_found(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.delete(f"/v1/nodes/{NODE}", headers=_user_ctx_header())
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /v1/nodes/add/worker
# ---------------------------------------------------------------------------


class TestAddWorker:
    def test_happy(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.post(
            "/v1/nodes/add/worker",
            json={"node_name": "host-1", "labels": {"zone": "eu"}},
            headers=_user_ctx_header(),
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["node_id"]
        assert data["bootstrap_token"] == "fake-bootstrap-token"
        assert "env_snippet" in data
        assert "BOOTSTRAP_TOKEN=fake-bootstrap-token" in data["env_snippet"]

    def test_validation_missing_node_name(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.post(
            "/v1/nodes/add/worker", json={},
            headers=_user_ctx_header(),
        )
        assert r.status_code == 422

    def test_missing_org_header(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.post(
            "/v1/nodes/add/worker",
            json={"node_name": "x"},
            headers={"Authorization": "Bearer deployment:list,deployment:create",
                     "X-User-ID": "u"},
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /v1/nodes/add/nosana
# ---------------------------------------------------------------------------


class TestAddNosana:
    def test_happy(self, app_and_deps):
        app, inventory, nosana, _akash = app_and_deps
        client = TestClient(app)
        r = client.post(
            "/v1/nodes/add/nosana",
            json={
                "gpu_type": "RTX4090",
                "market_address": "Ab...",
                "credential_name": "default",
                "labels": {"zone": "eu"},
            },
            headers=_user_ctx_header(),
        )
        assert r.status_code == 200, r.text
        assert len(nosana.calls) == 1
        body = r.json()
        assert body["node_id"]
        assert body["state"] == "provisioning"

    def test_unknown_provider(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.post(
            "/v1/nodes/add/unknownprovider",
            json={}, headers=_user_ctx_header(),
        )
        assert r.status_code == 404

    def test_adapter_failure_bubbles(self, app_and_deps):
        app, inventory, nosana, _akash = app_and_deps
        nosana.raises = RuntimeError("nosana SDK boom")
        client = TestClient(app)
        r = client.post(
            "/v1/nodes/add/nosana",
            json={"gpu_type": "RTX", "market_address": "x", "credential_name": "default"},
            headers=_user_ctx_header(),
        )
        assert r.status_code == 502
