"""End-to-end tests for the three provisioning REST endpoints.

Uses FastAPI TestClient against an isolated app that wires nodes_api
with mock repos and a mock AWS adapter.
"""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from datetime import datetime, timezone
import json

import pytest
from fastapi import FastAPI

# Repo-wide version skew: starlette 0.35.1 still passes ``app=`` to
# ``httpx.Client``, which httpx 0.28+ removed. Patch the httpx Client
# constructor to drop the ``app`` kwarg for the duration of this test
# module so the existing sync TestClient-based tests keep working.
import httpx as _httpx
_orig_client_init = _httpx.Client.__init__


def _patched_client_init(self, *args, **kwargs):
    kwargs.pop("app", None)
    return _orig_client_init(self, *args, **kwargs)


_httpx.Client.__init__ = _patched_client_init  # type: ignore[assignment]
from fastapi.testclient import TestClient  # noqa: E402

from services.orchestration.api import nodes as nodes_api  # noqa: E402


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
    # T24 added a get_by_node lookup; tests in this file predate the
    # provisioning_jobs queue and want the legacy current_phase/state
    # fallback. Returning None keeps that path active.
    prov.get_by_node = AsyncMock(return_value=None)
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


# ---------------------------------------------------------------------------
# Region-resolution tests for get_ec2_console (Task 3 coverage).
#
# Each test exercises one tier of the priority chain:
#   T1: job.spec["region"]            (highest priority)
#   T2: job.pulumi_stack_outputs["region"]
#   T3: node metadata["region"] (dict)
#   T4: node metadata["region"] (JSON string — parse path)
#   T5: pool region_constraint[0]
#   T6: no region anywhere → region=None
#
# All tests assert get_logs.await_args.kwargs["region"] explicitly so that
# a future typo in the kwarg name is caught even though AsyncMock accepts
# any kwarg without raising (AsyncMock-signature-blindness trap).
# ---------------------------------------------------------------------------


def _make_job(spec_region=None, outputs_region=None):
    """Return a MagicMock that looks like a ProvisioningJob row."""
    job = MagicMock()
    job.spec = {"region": spec_region} if spec_region else {}
    job.pulumi_stack_outputs = {"region": outputs_region} if outputs_region else {}
    return job


def _build_region_app(
    *,
    node_row_extra=None,
    job=None,
    pool_row=None,
):
    """Build a minimal FastAPI app for region-resolution tests.

    ``node_row_extra`` is merged into the default node dict so callers can
    override ``metadata``, etc.  ``job`` is returned by
    ``provisioning_repo.get_by_node``.  ``pool_row`` is returned by
    ``pool_repo.get``; if it is a plain dict it is used as-is (the real code
    calls ``pool.get("region_constraint")``).
    """
    pool_id = uuid4()
    node_row = {
        "id": uuid4(),
        "pool_id": pool_id,
        "node_name": "n-region",
        "agent_kind": None,
        "provider": "aws",
        "state": "ready",
        "labels": {},
        "advertise_url": None,
        "expose_url": None,
        "gpu_total": 0,
        "gpu_allocated": 0,
        "vcpu_total": 0,
        "vcpu_allocated": 0,
        "ram_gb_total": 0,
        "ram_gb_allocated": 0,
        "last_heartbeat": None,
        # Real instance ID so the placeholder-skip branch is not taken.
        "provider_instance_id": "i-region123",
        "metadata": {},
    }
    if node_row_extra:
        node_row.update(node_row_extra)

    inv = MagicMock()
    inv.get_node = AsyncMock(return_value=node_row)

    prov = MagicMock()
    prov.get_by_node = AsyncMock(return_value=job)
    # Legacy node_events_repo methods (used by other endpoints; not exercised
    # by ec2-console but needed so configure() doesn't choke).
    prov.summarize_phases = AsyncMock(return_value=[])
    prov.list_events_after = AsyncMock(return_value=[])
    prov.current_phase = AsyncMock(return_value=None)

    pool_repo_mock = MagicMock()
    pool_repo_mock.get = AsyncMock(return_value=pool_row)

    aws_adapter = MagicMock()
    aws_adapter.get_logs = AsyncMock(return_value={"logs": ["line1"]})

    app = FastAPI()
    nodes_api.configure(
        inventory_repo=inv,
        pool_repo=pool_repo_mock,
        worker_auth=MagicMock(),
        control_plane_external_url="",
        adapters={"aws": aws_adapter},
        require_permission=lambda _: (lambda: True),
        provisioning_repo=prov,
    )
    app.include_router(nodes_api.router)
    return app, node_row, aws_adapter


def test_region_from_job_spec(app_and_deps):
    """Tier 1: job.spec['region'] is used when present."""
    _, _, prov, aws_adapter, node_row = app_and_deps
    node_row["provider_instance_id"] = "i-spec-region"

    job = _make_job(spec_region="eu-west-1")
    prov.get_by_node = AsyncMock(return_value=job)

    app = FastAPI()
    nodes_api.configure(
        inventory_repo=MagicMock(**{"get_node": AsyncMock(return_value=node_row)}),
        pool_repo=MagicMock(),
        worker_auth=MagicMock(),
        control_plane_external_url="",
        adapters={"aws": aws_adapter},
        require_permission=lambda _: (lambda: True),
        provisioning_repo=prov,
    )
    app.include_router(nodes_api.router)

    client = TestClient(app)
    r = client.get(
        f"/v1/nodes/{node_row['id']}/ec2-console",
        headers=_user_ctx_header(),
    )
    assert r.status_code == 200
    assert aws_adapter.get_logs.await_args.kwargs["region"] == "eu-west-1"


def test_region_from_job_pulumi_outputs():
    """Tier 2: job.pulumi_stack_outputs['region'] when spec has no region."""
    job = _make_job(spec_region=None, outputs_region="ap-southeast-1")
    app, node_row, aws_adapter = _build_region_app(job=job)

    client = TestClient(app)
    r = client.get(
        f"/v1/nodes/{node_row['id']}/ec2-console",
        headers=_user_ctx_header(),
    )
    assert r.status_code == 200
    assert aws_adapter.get_logs.await_args.kwargs["region"] == "ap-southeast-1"


def test_region_from_node_metadata_dict():
    """Tier 3: node metadata dict has 'region' when no job region."""
    app, node_row, aws_adapter = _build_region_app(
        node_row_extra={"metadata": {"region": "us-west-2"}},
        job=None,
    )

    client = TestClient(app)
    r = client.get(
        f"/v1/nodes/{node_row['id']}/ec2-console",
        headers=_user_ctx_header(),
    )
    assert r.status_code == 200
    assert aws_adapter.get_logs.await_args.kwargs["region"] == "us-west-2"


def test_region_from_node_metadata_json_string():
    """Tier 3 parse path: metadata is a JSON string (asyncpg text column)."""
    metadata_str = json.dumps({"region": "ca-central-1"})
    app, node_row, aws_adapter = _build_region_app(
        node_row_extra={"metadata": metadata_str},
        job=None,
    )

    client = TestClient(app)
    r = client.get(
        f"/v1/nodes/{node_row['id']}/ec2-console",
        headers=_user_ctx_header(),
    )
    assert r.status_code == 200
    assert aws_adapter.get_logs.await_args.kwargs["region"] == "ca-central-1"


def test_region_from_pool_region_constraint():
    """Tier 4: pool.region_constraint[0] when job + metadata have no region."""
    pool_row = {"region_constraint": ["eu-central-1", "eu-west-1"]}
    app, node_row, aws_adapter = _build_region_app(
        node_row_extra={"metadata": {}},
        job=None,
        pool_row=pool_row,
    )

    client = TestClient(app)
    r = client.get(
        f"/v1/nodes/{node_row['id']}/ec2-console",
        headers=_user_ctx_header(),
    )
    assert r.status_code == 200
    assert aws_adapter.get_logs.await_args.kwargs["region"] == "eu-central-1"


def test_region_none_when_no_source_available():
    """Tier fallback: region=None is passed to get_logs when all sources are empty."""
    app, node_row, aws_adapter = _build_region_app(
        node_row_extra={"metadata": {}},
        job=None,
        pool_row={"region_constraint": []},
    )

    client = TestClient(app)
    r = client.get(
        f"/v1/nodes/{node_row['id']}/ec2-console",
        headers=_user_ctx_header(),
    )
    assert r.status_code == 200
    assert aws_adapter.get_logs.await_args.kwargs["region"] is None


def test_region_job_spec_takes_priority_over_metadata():
    """Tier 1 wins even when metadata also has a (different) region."""
    job = _make_job(spec_region="us-east-1")
    app, node_row, aws_adapter = _build_region_app(
        node_row_extra={"metadata": {"region": "us-west-2"}},
        job=job,
    )

    client = TestClient(app)
    r = client.get(
        f"/v1/nodes/{node_row['id']}/ec2-console",
        headers=_user_ctx_header(),
    )
    assert r.status_code == 200
    assert aws_adapter.get_logs.await_args.kwargs["region"] == "us-east-1"


def test_region_metadata_takes_priority_over_pool():
    """Tier 3 wins over tier 4: metadata region beats pool constraint."""
    pool_row = {"region_constraint": ["sa-east-1"]}
    app, node_row, aws_adapter = _build_region_app(
        node_row_extra={"metadata": {"region": "ap-northeast-1"}},
        job=None,
        pool_row=pool_row,
    )

    client = TestClient(app)
    r = client.get(
        f"/v1/nodes/{node_row['id']}/ec2-console",
        headers=_user_ctx_header(),
    )
    assert r.status_code == 200
    assert aws_adapter.get_logs.await_args.kwargs["region"] == "ap-northeast-1"
