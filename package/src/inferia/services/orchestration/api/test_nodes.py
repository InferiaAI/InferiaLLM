"""Tests for /v1/nodes/* router."""

from __future__ import annotations

import time
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

# Repo-wide version skew: starlette 0.35.1's TestClient still passes
# ``app=`` to ``httpx.Client``, which httpx 0.28+ removed. We patch
# httpx.Client.__init__ to silently drop the ``app`` kwarg for the
# duration of this module so the existing sync TestClient-based tests
# in this file keep working. New tests for the AWS delete path use
# httpx.AsyncClient + ASGITransport directly.
import httpx as _httpx
_orig_client_init = _httpx.Client.__init__


def _patched_client_init(self, *args, **kwargs):
    kwargs.pop("app", None)
    return _orig_client_init(self, *args, **kwargs)


_httpx.Client.__init__ = _patched_client_init  # type: ignore[assignment]
from fastapi.testclient import TestClient  # noqa: E402

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

    def test_default_advertise_url_is_compose_service_hostname(self, app_and_deps):
        # Regression: the default WORKER_ADVERTISE_URL must be
        # http://inferia-worker:8080 (the worker compose's service name on
        # deploy_inferia-net). http://localhost:8080 is wrong for the
        # sibling-compose case — localhost inside the control-plane
        # container resolves to itself, not the worker — and would cause
        # placement to silently fail every GPU deployment.
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.post(
            "/v1/nodes/add/worker",
            json={"node_name": "host-1"},
            headers=_user_ctx_header(),
        )
        assert r.status_code == 200, r.text
        snippet = r.json()["env_snippet"]
        assert "WORKER_ADVERTISE_URL=http://inferia-worker:8080\n" in snippet
        assert "WORKER_ADVERTISE_URL=http://localhost" not in snippet

    def test_explicit_advertise_url_overrides_default(self, app_and_deps):
        app, *_ = app_and_deps
        client = TestClient(app)
        r = client.post(
            "/v1/nodes/add/worker",
            json={
                "node_name": "host-1",
                "advertise_url": "https://gpu-1.prod.example.com:8443",
            },
            headers=_user_ctx_header(),
        )
        assert r.status_code == 200, r.text
        snippet = r.json()["env_snippet"]
        assert (
            "WORKER_ADVERTISE_URL=https://gpu-1.prod.example.com:8443\n" in snippet
        )

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


# ---------------------------------------------------------------------------
# POST /v1/nodes/add/aws — thin enqueue path.
# ---------------------------------------------------------------------------
#
# The AWS branch is fundamentally different from nosana/akash: it does NOT
# call adapter.provision_single_node (which would block on Pulumi for many
# seconds). Instead it validates the spec, writes a 'provisioning'
# placeholder row, enqueues a provisioning_jobs row, and returns. The
# reconciler picks the job up out-of-band.


class _FakeProvisioningRepo:
    """Captures enqueue() calls. Mirrors the prod async interface so
    AsyncMock isn't required for the happy-path test."""

    def __init__(self):
        self.enqueued: list[dict] = []

    async def enqueue(self, *, node_id, pool_id, org_id, provider, spec):
        job_id = uuid.uuid4()
        self.enqueued.append({
            "job_id": job_id,
            "node_id": node_id,
            "pool_id": pool_id,
            "org_id": org_id,
            "provider": provider,
            "spec": spec,
        })
        return job_id


@pytest.fixture
def aws_add_app_and_deps():
    """Configure the nodes router for the AWS thin-enqueue tests.

    Note: nodes_api.configure() reassigns _deps for every call, so this
    fixture overwrites any state left behind by the app_and_deps /
    aws_app_and_deps fixtures. As a defensive measure each test that
    overrides individual _deps fields (e.g. db_pool=None) reconfigures
    here from scratch — pytest tears the fixture down between tests so
    no cross-test leakage is possible.
    """
    app = FastAPI()
    inventory = FakeInventory()
    # Wire the placeholder-creator as an AsyncMock so we can assert on
    # the call args without writing a real implementation in FakeInventory.
    placeholder_id = uuid.UUID(NODE)
    inventory.create_provisioning_placeholder = AsyncMock(
        return_value=placeholder_id,
    )
    pool_repo = FakePoolRepo()
    provisioning_repo = _FakeProvisioningRepo()
    nodes_api.configure(
        inventory_repo=inventory,
        pool_repo=pool_repo,
        worker_auth=FakeWorkerAuth(),
        control_plane_external_url="https://control.example.com",
        adapters={},  # aws goes through the enqueue path, not the adapter
        require_permission=fake_require_permission,
        provisioning_repo=provisioning_repo,
    )
    app.include_router(nodes_api.router)
    return app, inventory, provisioning_repo


class TestAddAwsNode:
    def test_add_aws_node_returns_node_id_and_job_id_in_under_one_second(
        self, aws_add_app_and_deps,
    ):
        """The HTTP path must NOT block on Pulumi; should return
        immediately. We give a generous 1s budget for the FastAPI
        round-trip; the real path runs in ~200ms."""
        app, inventory, provisioning_repo = aws_add_app_and_deps
        client = TestClient(app)
        body = {
            "spec": {
                "instance_class": "normal_gpu",
                "instance_type":  "g6.xlarge",
                "region":         "us-east-1",
            },
        }
        start = time.monotonic()
        resp = client.post(
            "/v1/nodes/add/aws", json=body, headers=_user_ctx_header(),
        )
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"add/aws took {elapsed:.2f}s"
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "node_id" in data
        assert "job_id" in data
        assert data["node_id"] == NODE
        assert data["provider"] == "aws"
        assert data["provider_instance_id"] is None
        assert data["state"] == "provisioning"
        # Inventory placeholder was created exactly once.
        inventory.create_provisioning_placeholder.assert_awaited_once()
        kwargs = inventory.create_provisioning_placeholder.call_args.kwargs
        assert kwargs["provider"] == "aws"
        assert kwargs["instance_class"] == "normal_gpu"
        assert kwargs["instance_type"] == "g6.xlarge"
        assert kwargs["pool_id"] == POOL
        # Provisioning job enqueued exactly once.
        assert len(provisioning_repo.enqueued) == 1
        job = provisioning_repo.enqueued[0]
        assert job["provider"] == "aws"
        assert job["node_id"] == uuid.UUID(NODE)
        assert job["pool_id"] == POOL
        assert job["spec"]["instance_class"] == "normal_gpu"
        assert job["spec"]["instance_type"] == "g6.xlarge"
        assert job["spec"]["region"] == "us-east-1"
        # job_id is the one the fake returned.
        assert data["job_id"] == str(job["job_id"])

    def test_add_aws_node_rejects_missing_instance_class(
        self, aws_add_app_and_deps,
    ):
        app, *_ = aws_add_app_and_deps
        client = TestClient(app)
        resp = client.post(
            "/v1/nodes/add/aws",
            json={"spec": {"instance_type": "g6.xlarge",
                           "region": "us-east-1"}},
            headers=_user_ctx_header(),
        )
        assert resp.status_code == 422, resp.text
        assert "instance_class" in resp.text

    def test_add_aws_node_rejects_missing_instance_type(
        self, aws_add_app_and_deps,
    ):
        app, *_ = aws_add_app_and_deps
        client = TestClient(app)
        resp = client.post(
            "/v1/nodes/add/aws",
            json={"spec": {"instance_class": "normal_gpu",
                           "region": "us-east-1"}},
            headers=_user_ctx_header(),
        )
        assert resp.status_code == 422, resp.text
        assert "instance_type" in resp.text

    def test_add_aws_node_rejects_missing_region(self, aws_add_app_and_deps):
        app, *_ = aws_add_app_and_deps
        client = TestClient(app)
        resp = client.post(
            "/v1/nodes/add/aws",
            json={"spec": {"instance_class": "normal_gpu",
                           "instance_type": "g6.xlarge"}},
            headers=_user_ctx_header(),
        )
        assert resp.status_code == 422, resp.text
        assert "region" in resp.text

    def test_add_aws_node_rejects_unknown_instance_type(
        self, aws_add_app_and_deps,
    ):
        app, *_ = aws_add_app_and_deps
        client = TestClient(app)
        resp = client.post(
            "/v1/nodes/add/aws",
            json={"spec": {"instance_class": "normal_gpu",
                           "instance_type": "x99.unknown",
                           "region": "us-east-1"}},
            headers=_user_ctx_header(),
        )
        assert resp.status_code == 422, resp.text
        assert "x99.unknown" in resp.text

    def test_add_aws_node_rejects_class_type_mismatch(
        self, aws_add_app_and_deps,
    ):
        """c6i.xlarge is a CPU type. Pairing it with normal_gpu must 422."""
        app, *_ = aws_add_app_and_deps
        client = TestClient(app)
        resp = client.post(
            "/v1/nodes/add/aws",
            json={"spec": {"instance_class": "normal_gpu",
                           "instance_type": "c6i.xlarge",
                           "region": "us-east-1"}},
            headers=_user_ctx_header(),
        )
        assert resp.status_code == 422, resp.text

    def test_add_aws_node_503_when_provisioning_repo_unconfigured(
        self, aws_add_app_and_deps,
    ):
        """If the orchestration boot did not wire provisioning_repo,
        fail loudly instead of silently dropping the job."""
        app, *_ = aws_add_app_and_deps
        nodes_api._deps.provisioning_repo = None
        client = TestClient(app)
        resp = client.post(
            "/v1/nodes/add/aws",
            json={"spec": {"instance_class": "normal_gpu",
                           "instance_type": "g6.xlarge",
                           "region": "us-east-1"}},
            headers=_user_ctx_header(),
        )
        assert resp.status_code == 503, resp.text


# ---------------------------------------------------------------------------
# DELETE /v1/nodes/{id} — AWS branch (destroys EC2).
# ---------------------------------------------------------------------------
#
# httpx AsyncClient is used here (instead of TestClient) to side-step a
# repo-wide TestClient/httpx version skew unrelated to this feature.


import httpx
from httpx import ASGITransport
from unittest.mock import AsyncMock, patch

from inferia.services.orchestration.services.adapter_engine import aws_deprovision


class FakeDbPool:
    def __init__(self):
        self.calls = []

    def acquire(self):
        class _Ctx:
            async def __aenter__(_self):
                class _Conn:
                    async def execute(_c, *a, **kw):
                        pass
                return _Conn()
            async def __aexit__(_self, *a):
                return None
        return _Ctx()


class AwsFakeInventory(FakeInventory):
    """FakeInventory extended with mark_terminating_node + provider awareness."""

    def __init__(self):
        super().__init__()
        self.terminating_calls: list[str] = []

    async def mark_terminating_node(self, *, node_id):
        self.terminating_calls.append(node_id)
        if node_id in self.nodes:
            self.nodes[node_id]["state"] = "terminating"


@pytest.fixture
def aws_app_and_deps():
    """Configure the nodes router with a pool + an AWS-aware inventory."""
    app = FastAPI()
    inventory = AwsFakeInventory()
    pool_repo = FakePoolRepo()
    nodes_api.configure(
        inventory_repo=inventory,
        pool_repo=pool_repo,
        worker_auth=FakeWorkerAuth(),
        control_plane_external_url="https://control.example.com",
        adapters={},
        require_permission=fake_require_permission,
        db_pool=FakeDbPool(),
    )
    app.include_router(nodes_api.router)
    return app, inventory


class TestDeleteAwsNode:
    @pytest.mark.asyncio
    async def test_aws_node_returns_202_and_kicks_destroy(self, aws_app_and_deps):
        app, inventory = aws_app_and_deps
        inventory.nodes[NODE] = {
            "id": NODE, "state": "ready", "labels": {},
            "pool_id": POOL, "provider": "aws",
        }
        # _spawn_destroy returns a Task in real code; the route does not
        # await it (the destroy is a fire-and-forget background task), so
        # we just spy on the call and return a sentinel.
        spy = []

        class _FakeTask:
            pass

        def fake_spawn(**kw):
            spy.append(kw)
            return _FakeTask()

        with patch.object(aws_deprovision, "_spawn_destroy", side_effect=fake_spawn):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t",
            ) as c:
                r = await c.delete(
                    f"/v1/nodes/{NODE}", headers=_user_ctx_header(),
                )

        assert r.status_code == 202, r.text
        body = r.json()
        assert body["node_id"] == NODE
        assert body["state"] == "terminating"
        assert inventory.terminating_calls == [NODE]
        # Helper was invoked with pool_id from the node row.
        assert len(spy) == 1
        assert spy[0]["pool_id"] == POOL
        assert spy[0]["node_id"] == NODE

    @pytest.mark.asyncio
    async def test_non_aws_node_keeps_204_softdelete(self, aws_app_and_deps):
        app, inventory = aws_app_and_deps
        inventory.nodes[NODE] = {
            "id": NODE, "state": "ready", "labels": {},
            "pool_id": POOL, "provider": "on_prem",
        }
        with patch.object(aws_deprovision, "_spawn_destroy") as spawn:
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t",
            ) as c:
                r = await c.delete(
                    f"/v1/nodes/{NODE}", headers=_user_ctx_header(),
                )
        assert r.status_code == 204
        assert inventory.nodes[NODE]["state"] == "terminated"
        spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_aws_node_without_db_pool_falls_back_to_softdelete(
        self, aws_app_and_deps,
    ):
        """If the orchestration boot didn't wire db_pool, still succeed."""
        app, inventory = aws_app_and_deps
        # Strip the db_pool to simulate a misconfiguration.
        nodes_api._deps.db_pool = None
        inventory.nodes[NODE] = {
            "id": NODE, "state": "ready", "labels": {},
            "pool_id": POOL, "provider": "aws",
        }
        with patch.object(aws_deprovision, "_spawn_destroy") as spawn:
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t",
            ) as c:
                r = await c.delete(
                    f"/v1/nodes/{NODE}", headers=_user_ctx_header(),
                )
        # Without db_pool we cannot kick destroy. Soft-delete still runs
        # so the user sees the row disappear from the dashboard.
        assert r.status_code == 204
        spawn.assert_not_called()
        assert inventory.nodes[NODE]["state"] == "terminated"

    @pytest.mark.asyncio
    async def test_aws_node_missing_pool_id_returns_404_compat(
        self, aws_app_and_deps,
    ):
        app, inventory = aws_app_and_deps
        # Empty pool_id on the row — should still soft-delete and not
        # spawn a destroy (helper would no-op anyway, but we save the
        # work by not scheduling).
        inventory.nodes[NODE] = {
            "id": NODE, "state": "ready", "labels": {},
            "pool_id": None, "provider": "aws",
        }
        with patch.object(aws_deprovision, "_spawn_destroy") as spawn:
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t",
            ) as c:
                r = await c.delete(
                    f"/v1/nodes/{NODE}", headers=_user_ctx_header(),
                )
        # Soft-delete still runs; we can't destroy a stack without a pool_id.
        assert r.status_code == 204
        spawn.assert_not_called()
