"""End-to-end tests for the three provisioning REST endpoints.

Uses FastAPI TestClient against an isolated app that wires nodes_api
with mock repos and a mock AWS adapter.
"""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from inferia.services.orchestration.api import nodes as nodes_api


def _user_ctx_header():
    return {
        "authorization": "Bearer test",
        "x-organization-id": str(uuid4()),
    }


@pytest.fixture
def app_and_deps():
    inv = MagicMock()
    pool_id = uuid4()
    node_row = {
        "id": uuid4(), "pool_id": pool_id, "node_name": "n1",
        "agent_kind": None, "provider": "aws", "state": "provisioning",
        "labels": {}, "advertise_url": None, "expose_url": None,
        "gpu_total": 0, "gpu_allocated": 0, "vcpu_total": 0, "vcpu_allocated": 0,
        "ram_gb_total": 0, "ram_gb_allocated": 0, "last_heartbeat": None,
        "provider_instance_id": "placeholder:" + str(pool_id),
    }
    inv.get_node = AsyncMock(return_value=node_row)
    prov = MagicMock()
    prov.summarize_phases = AsyncMock(return_value=[
        {"phase": "prepare", "status": "succeeded",
         "started_at": datetime.now(timezone.utc),
         "ended_at":   datetime.now(timezone.utc),
         "last_message": None},
        {"phase": "pulumi_up", "status": "running",
         "started_at": datetime.now(timezone.utc),
         "ended_at": None,
         "last_message": "creating ec2"},
    ])
    prov.list_events_after = AsyncMock(return_value=[
        {"id": 1, "phase": "prepare", "status": "running",
         "message": None, "created_at": datetime.now(timezone.utc)},
        {"id": 2, "phase": "prepare", "status": "succeeded",
         "message": None, "created_at": datetime.now(timezone.utc)},
    ])
    prov.current_phase = AsyncMock(return_value="pulumi_up")
    aws_adapter = MagicMock()
    aws_adapter.get_logs = AsyncMock(return_value={
        "logs": ["[boot] cloud-init starting", "[user-data] docker pull..."],
    })
    app = FastAPI()
    nodes_api.configure(
        inventory_repo=inv, pool_repo=MagicMock(), worker_auth=MagicMock(),
        control_plane_external_url="", adapters={"aws": aws_adapter},
        require_permission=lambda _: (lambda: True),
        provisioning_repo=prov,
    )
    # nodes_api.router already has prefix="/v1/nodes" baked in
    app.include_router(nodes_api.router)
    return app, inv, prov, aws_adapter, node_row


def test_get_provisioning_returns_phase_summary(app_and_deps):
    app, _, _, _, node_row = app_and_deps
    client = TestClient(app)
    r = client.get(f"/v1/nodes/{node_row['id']}/provisioning",
                   headers=_user_ctx_header())
    assert r.status_code == 200
    body = r.json()
    assert body["current_phase"] == "pulumi_up"
    assert body["terminal"] is False
    phases = {p["phase"]: p for p in body["phases"]}
    assert phases["prepare"]["status"] == "succeeded"
    assert phases["pulumi_up"]["status"] == "running"


def test_get_provisioning_logs_returns_events_after_cursor(app_and_deps):
    app, _, _, _, node_row = app_and_deps
    client = TestClient(app)
    r = client.get(f"/v1/nodes/{node_row['id']}/provisioning-logs?after=0",
                   headers=_user_ctx_header())
    assert r.status_code == 200
    body = r.json()
    assert [e["id"] for e in body["events"]] == [1, 2]
    assert body["next_after"] == 2


def test_get_provisioning_logs_empty_returns_null_cursor(app_and_deps):
    app, _, prov, _, node_row = app_and_deps
    prov.list_events_after = AsyncMock(return_value=[])
    client = TestClient(app)
    r = client.get(f"/v1/nodes/{node_row['id']}/provisioning-logs?after=99",
                   headers=_user_ctx_header())
    body = r.json()
    assert body["events"] == []
    assert body["next_after"] is None


def test_get_ec2_console_proxies_adapter(app_and_deps):
    app, _, _, aws_adapter, node_row = app_and_deps
    # ec2-console only fires the boto3 fetch when the placeholder id has
    # been swapped for a real instance id. Override the fixture node's
    # provider_instance_id so the endpoint actually calls adapter.get_logs.
    node_row["provider_instance_id"] = "i-real123"
    client = TestClient(app)
    r = client.get(f"/v1/nodes/{node_row['id']}/ec2-console",
                   headers=_user_ctx_header())
    assert r.status_code == 200
    body = r.json()
    assert body["logs"][0].startswith("[boot]")
    assert "fetched_at" in body
    aws_adapter.get_logs.assert_awaited_once()


def test_endpoints_404_when_node_missing(app_and_deps):
    app, inv, _, _, _ = app_and_deps
    inv.get_node = AsyncMock(return_value=None)
    client = TestClient(app)
    bogus = uuid4()
    for path in ("provisioning", "provisioning-logs", "ec2-console"):
        r = client.get(f"/v1/nodes/{bogus}/{path}", headers=_user_ctx_header())
        assert r.status_code == 404, path


def test_ec2_console_404_for_non_aws_node(app_and_deps):
    app, inv, _, _, node_row = app_and_deps
    node_row["provider"] = "worker"
    inv.get_node = AsyncMock(return_value=node_row)
    client = TestClient(app)
    r = client.get(f"/v1/nodes/{node_row['id']}/ec2-console",
                   headers=_user_ctx_header())
    assert r.status_code == 404
    assert "aws" in r.json()["detail"].lower()


def test_ec2_console_returns_empty_when_instance_still_placeholder(app_and_deps):
    """While placeholder:%, the swap hasn't happened — return empty without
    calling boto3 (avoid an InvalidInstanceID error)."""
    app, _, _, aws_adapter, node_row = app_and_deps
    client = TestClient(app)
    r = client.get(f"/v1/nodes/{node_row['id']}/ec2-console",
                   headers=_user_ctx_header())
    assert r.status_code == 200
    assert r.json()["logs"] == []
    aws_adapter.get_logs.assert_not_awaited()


def test_provisioning_terminal_true_when_state_ready(app_and_deps):
    """Even with worker_bootstrap stuck at 'running', terminal=True
    once the inventory row transitions to 'ready'."""
    app, _, prov, _, node_row = app_and_deps
    node_row["state"] = "ready"
    prov.current_phase = AsyncMock(return_value="worker_bootstrap")
    client = TestClient(app)
    r = client.get(f"/v1/nodes/{node_row['id']}/provisioning",
                   headers=_user_ctx_header())
    assert r.json()["terminal"] is True


def test_provisioning_terminal_true_when_state_terminated(app_and_deps):
    app, _, prov, _, node_row = app_and_deps
    node_row["state"] = "terminated"
    prov.current_phase = AsyncMock(return_value=None)
    client = TestClient(app)
    r = client.get(f"/v1/nodes/{node_row['id']}/provisioning",
                   headers=_user_ctx_header())
    assert r.json()["terminal"] is True


def test_provisioning_routes_registered():
    paths = [getattr(r, "path", "") for r in nodes_api.router.routes]
    assert any(p.endswith("/provisioning") for p in paths)
    assert any(p.endswith("/provisioning-logs") for p in paths)
    assert any(p.endswith("/ec2-console") for p in paths)
