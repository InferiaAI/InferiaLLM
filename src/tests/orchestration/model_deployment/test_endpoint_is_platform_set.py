"""A deployment in a pool takes its endpoint from the platform, never the caller.

Inference resolves the hostname only of an external deployment's endpoint, the
one a user typed. A user-set endpoint on a pool deployment would get only the
literal checks, and a name like 169.254.169.254.nip.io passes those.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestration.models.model_deployment import deployment_server as ds

ORG = "org-a"
DEP = str(uuid4())
REFUSED = "endpoint can only be set on external deployments"


@pytest.fixture
def client(monkeypatch):
    async def _owner(kind, resource_id):
        return ORG if (kind, resource_id) == ("deployment", DEP) else None

    monkeypatch.setattr(ds, "_lookup_org_id", _owner)
    app = FastAPI()
    app.include_router(ds.router)
    app.state.pool = None
    app.state.worker_controller = None
    return TestClient(app, raise_server_exceptions=False, headers={"X-Organization-ID": ORG})


def _deploy_body(**over):
    body = {"model_name": "m", "model_version": "latest", "replicas": 1,
            "gpu_per_replica": 0, "pool_id": str(uuid4())}
    body.update(over)
    return body


@pytest.mark.parametrize("endpoint", [
    "http://169.254.169.254.nip.io/", "http://metadata.google.internal./",
    "http://inferia-worker:8080",
])
def test_deploy_to_a_pool_refuses_an_endpoint(client, endpoint):
    r = client.post("/deployment/deploy", json=_deploy_body(endpoint=endpoint))

    assert r.status_code == 422
    assert REFUSED in r.json()["detail"]


def test_deploy_to_a_pool_without_an_endpoint_gets_past(client):
    r = client.post("/deployment/deploy", json=_deploy_body())

    assert REFUSED not in r.text


def test_an_external_deploy_still_takes_its_endpoint(client, monkeypatch):
    seen = {}

    async def _no_duplicate(*a, **kw):
        return None

    async def _create(req, deploys):
        seen["endpoint"] = req.endpoint
        return {"deployment_id": DEP, "state": "RUNNING"}, 200

    async def _audit(**kw):
        return None

    monkeypatch.setattr(ds, "_reject_duplicate_deployment", _no_duplicate)
    monkeypatch.setattr(ds, "_create_external_deployment", _create)
    monkeypatch.setattr(ds, "log_audit_event", _audit)

    r = client.post("/deployment/deploy", json=_deploy_body(
        workload_type="external", engine="custom", endpoint="https://api.example.com/v1"))

    assert r.status_code == 200
    assert seen["endpoint"] == "https://api.example.com/v1"


@pytest.mark.parametrize("in_a_pool, refused", [(True, True), (False, False)])
def test_update_refuses_an_endpoint_only_in_a_pool(client, monkeypatch, in_a_pool, refused):
    async def _pool(request, deployment_id):
        return in_a_pool

    monkeypatch.setattr(ds, "_in_a_pool", _pool)

    r = client.patch(f"/deployment/update/{DEP}", json={"endpoint": "http://localhost./"})

    assert (REFUSED in r.text) is refused


def test_update_without_an_endpoint_does_not_look_up_the_pool(client, monkeypatch):
    async def _pool(request, deployment_id):
        raise AssertionError("looked up the pool")

    monkeypatch.setattr(ds, "_in_a_pool", _pool)

    r = client.patch(f"/deployment/update/{DEP}", json={"replicas": 2})

    assert REFUSED not in r.text


@pytest.mark.asyncio
async def test_an_unreadable_deployment_counts_as_in_a_pool():
    """Fail closed: if the row cannot be read, the endpoint change is refused."""
    from types import SimpleNamespace

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=None)))
    assert await ds._in_a_pool(request, "not-a-uuid") is True
