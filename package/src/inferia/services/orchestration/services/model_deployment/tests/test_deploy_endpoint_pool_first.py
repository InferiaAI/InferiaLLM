"""Integration tests for the pool-first /deploy endpoint (T7).

These tests spin up an isolated FastAPI app with the deployment_server router,
set app.state.pool to a real asyncpg pool against inferia_test, and mock
app.state.worker_controller.  All DB state is seeded fresh per test (unique
UUIDs).

Run with:
    TEST_DATABASE_URL=postgresql://inferia:inferia@172.18.0.3:5432/inferia_test \\
    PYTHONPATH=package/src \\
    python -m pytest \\
      package/src/inferia/services/orchestration/services/model_deployment/tests/test_deploy_endpoint_pool_first.py -v
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock
from uuid import uuid4, UUID

import asyncpg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from inferia.services.orchestration.services.model_deployment import (
    deployment_server,
)
from inferia.services.orchestration.services.worker_controller.controller import (
    WorkerController,
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://inferia:inferia@localhost:5432/inferia_test",
)


@pytest_asyncio.fixture
async def db_pool():
    """Real asyncpg pool connected to the test database."""
    p = await asyncpg.create_pool(dsn=_DSN, min_size=1, max_size=4)
    yield p
    await p.close()


@pytest_asyncio.fixture
async def app_and_pool(db_pool):
    """Isolated FastAPI app with the deployment router mounted."""
    app = FastAPI()
    app.state.pool = db_pool
    app.state.worker_controller = AsyncMock(spec=WorkerController)
    app.state.event_bus = None
    app.include_router(deployment_server.router)
    yield app, db_pool


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

async def _seed_pool(
    pool,
    *,
    gpu_count: int = 4,
    max_nodes: int | None = None,
    provider: str = "aws",
    lifecycle_state: str = "running",
    metadata: dict | None = None,
) -> UUID:
    """Insert a compute_pool row, return its UUID."""
    org_id = uuid4()
    pool_id = uuid4()
    meta_json = json.dumps(metadata or {})
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO organizations(id, name) VALUES($1, $2) ON CONFLICT DO NOTHING",
            str(org_id), f"o-{org_id}",
        )
        await c.execute(
            """INSERT INTO compute_pools(
                   id, pool_name, owner_type, owner_id, provider, pool_type,
                   allowed_gpu_types, max_cost_per_hour, scheduling_policy,
                   provider_pool_id, is_active, lifecycle_state, gpu_count,
                   max_nodes, metadata
               )
               VALUES ($1, $2, 'organization', $3::text, $4, 'cluster',
                       ARRAY['none'], 0, '{}', $5, true, $6, $7, $8, $9::jsonb)""",
            pool_id, f"p-{pool_id}", str(org_id), provider,
            f"placeholder:{pool_id}", lifecycle_state, gpu_count, max_nodes,
            meta_json,
        )
    return pool_id


async def _seed_node(
    pool,
    pool_id: UUID,
    *,
    gpu_total: int = 4,
    gpu_allocated: int = 0,
    state: str = "ready",
) -> UUID:
    """Insert a compute_inventory row, return its UUID."""
    node_id = uuid4()
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO compute_inventory(
                   id, pool_id, provider, provider_instance_id, hostname,
                   node_name, agent_kind, gpu_total, gpu_allocated,
                   vcpu_total, vcpu_allocated, ram_gb_total, ram_gb_allocated,
                   state
               )
               VALUES ($1, $2, 'aws', $3, 'h', $4, 'worker',
                       $5, $6, 0, 0, 0, 0, $7)""",
            node_id, pool_id, f"p-{node_id}", f"n-{node_id}",
            gpu_total, gpu_allocated, state,
        )
    return node_id


async def _seed_ready_node(
    pool,
    pool_id: UUID,
    *,
    gpu_total: int = 4,
    gpu_allocated: int = 0,
) -> UUID:
    """Alias of _seed_node with state='ready' for clarity."""
    return await _seed_node(pool, pool_id, gpu_total=gpu_total,
                            gpu_allocated=gpu_allocated, state="ready")


def _deploy_payload(pool_id: UUID, *, gpu_per_replica: int = 1) -> dict:
    return {
        "model_name": "test-model",
        "model_version": "v1",
        "replicas": 1,
        "gpu_per_replica": gpu_per_replica,
        "pool_id": str(pool_id),
        "engine": "vllm",
        # model_name doubles as artifact_uri fallback; provide explicit
        # configuration.artifact_uri so the spec helper can resolve it.
        "configuration": {"artifact_uri": "hf://test-model"},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_deploy_to_empty_pool_returns_pending_node(app_and_pool):
    """ColdStart path: empty pool => PENDING_NODE + one provisioning job + one placeholder."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/deployment/deploy", json=_deploy_payload(pool_id))

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["state"] == "PENDING_NODE"
    assert "target_node_id" in body
    deploy_id = UUID(body["deployment_id"])
    node_id = UUID(body["target_node_id"])

    async with pool.acquire() as c:
        deploy_row = await c.fetchrow(
            "SELECT state, target_pool_id, target_node_id FROM model_deployments "
            "WHERE deployment_id=$1",
            deploy_id,
        )
        assert deploy_row["state"] == "PENDING_NODE"
        assert deploy_row["target_pool_id"] == pool_id
        assert deploy_row["target_node_id"] == node_id

        job_count = await c.fetchval(
            "SELECT COUNT(*) FROM provisioning_jobs WHERE pool_id=$1", pool_id,
        )
        assert job_count == 1, "expected exactly one ProvisioningJob"

        inv_count = await c.fetchval(
            "SELECT COUNT(*) FROM compute_inventory WHERE pool_id=$1 AND state='provisioning'",
            pool_id,
        )
        assert inv_count == 1, "expected exactly one placeholder node"


async def test_deploy_to_warm_pool_returns_deploying(app_and_pool):
    """BindToReady path: pool with ready node => DEPLOYING, GPU allocated, load_model called."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)
    node_id = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=0)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/deployment/deploy",
            json=_deploy_payload(pool_id, gpu_per_replica=2),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "DEPLOYING"
    assert UUID(body["target_node_id"]) == node_id

    async with pool.acquire() as c:
        inv_row = await c.fetchrow(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", node_id,
        )
        assert inv_row["gpu_allocated"] == 2

    controller = app.state.worker_controller
    controller.load_model.assert_awaited_once()
    call_kwargs = controller.load_model.await_args.kwargs
    assert "spec" in call_kwargs
    assert call_kwargs["spec"]["deployment_id"]  # non-empty
    assert call_kwargs["spec"]["model"]["artifact_uri"]  # non-empty


async def test_deploy_to_terminating_pool_returns_409(app_and_pool):
    """Deploying to a terminating pool must return 409."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool, lifecycle_state="terminating")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/deployment/deploy", json=_deploy_payload(pool_id))

    assert resp.status_code == 409, resp.text


async def test_deploy_at_max_nodes_returns_503(app_and_pool):
    """PoolAtCapacity path: pool at max_nodes returns 503 with POOL_AT_CAPACITY body."""
    app, pool = app_and_pool
    # max_nodes=1, node fully allocated so no free slot; adding another node blocked.
    pool_id = await _seed_pool(pool, gpu_count=4, max_nodes=1)
    await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=4)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/deployment/deploy", json=_deploy_payload(pool_id))

    assert resp.status_code == 503, resp.text
    assert resp.headers.get("retry-after") == "60"
    body = resp.json()
    assert body["error"] == "POOL_AT_CAPACITY"
    assert "current_nodes" in body
    assert "max_nodes" in body
    assert "deployment_id" in body


async def test_deploy_to_worker_pool_pending_no_provisioning_job(app_and_pool):
    """ColdStart with worker-pool metadata: PENDING_NODE returned, zero ProvisioningJobs."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool, metadata={"agent_kind": "worker"})

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/deployment/deploy", json=_deploy_payload(pool_id))

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["state"] == "PENDING_NODE"
    assert body.get("message") == "waiting for worker registration"

    async with pool.acquire() as c:
        job_count = await c.fetchval(
            "SELECT COUNT(*) FROM provisioning_jobs WHERE pool_id=$1", pool_id,
        )
    assert job_count == 0, "worker pool must NOT enqueue a ProvisioningJob"


async def test_deploy_duplicate_model_name_in_org_returns_409(app_and_pool):
    """Duplicate-name guard: same model_name + same org_id => 409 on second deploy."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool, gpu_count=4)
    await _seed_ready_node(pool, pool_id, gpu_total=4)
    org_id = str(uuid4())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First deploy succeeds (DEPLOYING — warm node present)
        r1 = await client.post("/deployment/deploy", json={
            "model_name": "qwen3",
            "model_version": "1.0",
            "replicas": 1,
            "gpu_per_replica": 1,
            "pool_id": str(pool_id),
            "engine": "vllm",
            "org_id": org_id,
            "configuration": {"artifact_uri": "hf://qwen3"},
        })
        assert r1.status_code == 200, r1.text

        # Second deploy with same model_name + same org => 409
        r2 = await client.post("/deployment/deploy", json={
            "model_name": "qwen3",
            "model_version": "1.0",
            "replicas": 1,
            "gpu_per_replica": 1,
            "pool_id": str(pool_id),
            "engine": "vllm",
            "org_id": org_id,
            "configuration": {"artifact_uri": "hf://qwen3"},
        })
        assert r2.status_code == 409, r2.text
        assert "already exists" in r2.text
