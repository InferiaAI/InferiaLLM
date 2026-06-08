"""Integration tests for the refcount-aware /terminate endpoint (T8).

These tests spin up an isolated FastAPI app with the deployment_server
router, set app.state.pool to a real asyncpg pool against inferia_test,
and mock app.state.worker_controller.  All DB state is seeded fresh per
test (unique UUIDs).

Run with:
    TEST_DATABASE_URL=postgresql://inferia:inferia@172.18.0.3:5432/inferia_test \\
    PYTHONPATH=package/src \\
    python -m pytest \\
      package/src/inferia/services/orchestration/services/model_deployment/tests/test_terminate_endpoint_refcount.py -v
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch
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


async def _seed_deploy(
    pool,
    pool_id: UUID,
    *,
    state: str = "RUNNING",
    gpu_per_replica: int = 1,
    target_node_id: UUID | None = None,
    org_id: str | None = None,
) -> UUID:
    """Insert a model_deployments row directly, return its UUID."""
    deploy_id = uuid4()
    if org_id is None:
        org_id = str(uuid4())
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO model_deployments(
                   deployment_id, model_name, replicas, gpu_per_replica,
                   pool_id, target_pool_id, target_node_id,
                   state, org_id
               )
               VALUES ($1, $2, 1, $3, $4, $4, $5, $6, $7)""",
            deploy_id, f"m-{deploy_id}", gpu_per_replica,
            pool_id, target_node_id, state, org_id,
        )
    return deploy_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_terminate_pending_node_unbinds_and_releases(app_and_pool):
    """PENDING_NODE path: unbind + release_gpu; node gpu_allocated drops; unload NOT called."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)
    # Node starts with gpu_allocated=1 (this deploy holds it)
    node_id = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=1, state="provisioning")
    deploy_id = await _seed_deploy(
        pool, pool_id,
        state="PENDING_NODE",
        gpu_per_replica=1,
        target_node_id=node_id,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/deployment/terminate", json={"deployment_id": str(deploy_id)}
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "TERMINATED"
    assert body["deployment_id"] == str(deploy_id)

    async with pool.acquire() as c:
        deploy_row = await c.fetchrow(
            "SELECT state, target_node_id FROM model_deployments WHERE deployment_id=$1",
            deploy_id,
        )
        assert deploy_row["state"] == "TERMINATED"
        assert deploy_row["target_node_id"] is None

        inv_row = await c.fetchrow(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", node_id,
        )
        assert inv_row["gpu_allocated"] == 0

    # unload_model must NOT be called for PENDING_NODE path
    controller = app.state.worker_controller
    controller.unload_model.assert_not_awaited()


async def test_terminate_running_calls_unload_and_releases(app_and_pool):
    """RUNNING path: unload_model called with correct kwargs; GPU released."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)
    node_id = await _seed_ready_node(pool, pool_id, gpu_total=4, gpu_allocated=2)
    deploy_id = await _seed_deploy(
        pool, pool_id,
        state="RUNNING",
        gpu_per_replica=2,
        target_node_id=node_id,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/deployment/terminate", json={"deployment_id": str(deploy_id)}
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "TERMINATED"

    async with pool.acquire() as c:
        deploy_row = await c.fetchrow(
            "SELECT state FROM model_deployments WHERE deployment_id=$1", deploy_id,
        )
        assert deploy_row["state"] == "TERMINATED"

        inv_row = await c.fetchrow(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", node_id,
        )
        assert inv_row["gpu_allocated"] == 0

    controller = app.state.worker_controller
    controller.unload_model.assert_awaited_once()
    call_kwargs = controller.unload_model.await_args.kwargs
    assert call_kwargs["node_id"] == str(node_id)
    assert call_kwargs["deployment_id"] == str(deploy_id)


async def test_terminate_last_deploy_triggers_destroy(app_and_pool):
    """Last reference released => metadata.terminating='true' + the EXISTING
    provisioning job is force_cancelled (so the reconciler's CancelHandler runs
    pulumi destroy). NOT a new enqueue — that inserted a preflight job which the
    reconciler tried to PROVISION, leaking the EC2."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool, provider="aws")
    node_id = await _seed_ready_node(pool, pool_id, gpu_total=4, gpu_allocated=1)
    deploy_id = await _seed_deploy(
        pool, pool_id,
        state="RUNNING",
        gpu_per_replica=1,
        target_node_id=node_id,
    )

    with patch(
        "inferia.services.orchestration.services.provisioning.jobs.repository."
        "ProvisioningJobRepository.force_cancel",
        new_callable=AsyncMock, return_value=True,
    ) as mock_force_cancel, patch(
        "inferia.services.orchestration.services.provisioning.jobs.repository."
        "ProvisioningJobRepository.enqueue",
        new_callable=AsyncMock,
    ) as mock_enqueue:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/deployment/terminate", json={"deployment_id": str(deploy_id)}
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "TERMINATED"

    # The destroy flips the EXISTING job via force_cancel(node_id=...), NOT enqueue.
    mock_force_cancel.assert_awaited_once()
    assert mock_force_cancel.await_args.kwargs["node_id"] == node_id   # UUID
    mock_enqueue.assert_not_awaited()

    # Node metadata must have terminating=true
    async with pool.acquire() as c:
        meta = await c.fetchval(
            "SELECT metadata->>'terminating' FROM compute_inventory WHERE id=$1",
            node_id,
        )
        assert meta == "true"

    # Deploy must be TERMINATED
    async with pool.acquire() as c:
        state = await c.fetchval(
            "SELECT state FROM model_deployments WHERE deployment_id=$1", deploy_id,
        )
        assert state == "TERMINATED"


async def test_terminate_with_other_deploys_does_not_destroy(app_and_pool):
    """Partial release: second deploy still holds the node => no destroy enqueued."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool, provider="aws")
    node_id = await _seed_ready_node(pool, pool_id, gpu_total=4, gpu_allocated=2)

    org_id = str(uuid4())
    deploy_id_0 = await _seed_deploy(
        pool, pool_id,
        state="RUNNING",
        gpu_per_replica=1,
        target_node_id=node_id,
        org_id=org_id,
    )
    deploy_id_1 = await _seed_deploy(
        pool, pool_id,
        state="RUNNING",
        gpu_per_replica=1,
        target_node_id=node_id,
        org_id=org_id,
    )

    with patch(
        "inferia.services.orchestration.services.provisioning.jobs.repository."
        "ProvisioningJobRepository.enqueue",
        new_callable=AsyncMock,
    ) as mock_enqueue:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/deployment/terminate", json={"deployment_id": str(deploy_id_0)}
            )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "TERMINATED"

    # No destroy job — second deploy still alive
    mock_enqueue.assert_not_awaited()

    async with pool.acquire() as c:
        state_0 = await c.fetchval(
            "SELECT state FROM model_deployments WHERE deployment_id=$1", deploy_id_0,
        )
        state_1 = await c.fetchval(
            "SELECT state FROM model_deployments WHERE deployment_id=$1", deploy_id_1,
        )
        gpu_alloc = await c.fetchval(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", node_id,
        )

    assert state_0 == "TERMINATED"
    assert state_1 == "RUNNING"
    assert gpu_alloc == 1


async def test_concurrent_double_terminate_releases_gpu_once(app_and_pool):
    """Two concurrent terminates of the SAME deploy must release the GPU only
    once. Node has two 1-GPU deploys (gpu_allocated=2); terminating deploy A
    twice concurrently must leave gpu_allocated=1 (deploy B's), not 0 — the
    atomic TERMINATED claim guards the release."""
    import asyncio as _asyncio

    app, pool = app_and_pool
    pool_id = await _seed_pool(pool, provider="aws")
    node_id = await _seed_ready_node(pool, pool_id, gpu_total=4, gpu_allocated=2)
    org_id = str(uuid4())
    deploy_a = await _seed_deploy(
        pool, pool_id, state="RUNNING", gpu_per_replica=1,
        target_node_id=node_id, org_id=org_id,
    )
    # deploy B keeps the node referenced so should_destroy stays False.
    await _seed_deploy(
        pool, pool_id, state="RUNNING", gpu_per_replica=1,
        target_node_id=node_id, org_id=org_id,
    )

    with patch(
        "inferia.services.orchestration.services.provisioning.jobs.repository."
        "ProvisioningJobRepository.enqueue",
        new_callable=AsyncMock,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r1, r2 = await _asyncio.gather(
                client.post("/deployment/terminate",
                            json={"deployment_id": str(deploy_a)}),
                client.post("/deployment/terminate",
                            json={"deployment_id": str(deploy_a)}),
            )

    assert r1.status_code == 200 and r2.status_code == 200
    assert {r1.json()["status"], r2.json()["status"]} == {"TERMINATED"}
    async with pool.acquire() as c:
        gpu_alloc = await c.fetchval(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", node_id,
        )
    # Released exactly once (2 - 1), NOT twice (which would wrongly hit 0).
    assert gpu_alloc == 1, f"double-release: gpu_allocated={gpu_alloc}, expected 1"


async def test_terminate_already_terminal_is_noop(app_and_pool):
    """Terminal-state no-op: FAILED deploy returns current state without side effects."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)
    deploy_id = await _seed_deploy(pool, pool_id, state="FAILED", gpu_per_replica=1)

    with patch(
        "inferia.services.orchestration.services.provisioning.jobs.repository."
        "ProvisioningJobRepository.enqueue",
        new_callable=AsyncMock,
    ) as mock_enqueue:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/deployment/terminate", json={"deployment_id": str(deploy_id)}
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "FAILED"
    assert body["deployment_id"] == str(deploy_id)

    controller = app.state.worker_controller
    controller.unload_model.assert_not_awaited()
    mock_enqueue.assert_not_awaited()


async def test_terminate_not_found_returns_404(app_and_pool):
    """Unknown deployment_id => 404."""
    app, _ = app_and_pool
    random_id = str(uuid4())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/deployment/terminate", json={"deployment_id": random_id}
        )

    assert resp.status_code == 404, resp.text


async def test_terminate_invalid_uuid_returns_400(app_and_pool):
    """Malformed UUID => 400."""
    app, _ = app_and_pool
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deployment/terminate",
            json={"deployment_id": "not-a-uuid"},
        )
    assert r.status_code == 400


async def test_terminate_destroys_node_even_when_pool_soft_deleted(app_and_pool):
    """Pool is_active=FALSE between deploy and terminate — the destroy
    must still fire via the compute_inventory.provider fallback."""
    app, pool = app_and_pool
    # seed pool, ready node, running deploy; then soft-delete pool
    pool_id = await _seed_pool(pool, provider='aws', gpu_count=4)
    node_id = await _seed_ready_node(pool, pool_id, gpu_total=4, gpu_allocated=1)
    deploy_id = await _seed_deploy(pool, pool_id=pool_id,
                                    state='RUNNING',
                                    gpu_per_replica=1,
                                    target_node_id=node_id)
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE compute_pools SET is_active=FALSE WHERE id=$1", pool_id,
        )

    with patch(
        "inferia.services.orchestration.services.provisioning.jobs.repository."
        "ProvisioningJobRepository.force_cancel",
        new_callable=AsyncMock, return_value=True,
    ) as mock_force_cancel:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/deployment/terminate",
                json={"deployment_id": str(deploy_id)},
            )
    assert resp.status_code == 200
    mock_force_cancel.assert_awaited_once()
    assert mock_force_cancel.await_args.kwargs["node_id"] == node_id


async def test_terminate_failed_deploy_destroys_orphan_node(app_and_pool):
    """C9: a FAILED deploy that still owns the last reference to a live node
    must force_cancel that node so its EC2 doesn't leak (e.g. FAILED after a
    successful pulumi up / bootstrap timeout)."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool, provider="aws")
    node_id = await _seed_ready_node(pool, pool_id, gpu_total=4, gpu_allocated=0)
    deploy_id = await _seed_deploy(
        pool, pool_id, state="FAILED", gpu_per_replica=1, target_node_id=node_id,
    )
    with patch(
        "inferia.services.orchestration.services.provisioning.jobs.repository."
        "ProvisioningJobRepository.force_cancel",
        new_callable=AsyncMock, return_value=True,
    ) as mock_force_cancel:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/deployment/terminate", json={"deployment_id": str(deploy_id)}
            )
    assert resp.status_code == 200
    assert resp.json()["status"] == "FAILED"
    mock_force_cancel.assert_awaited_once()
    assert mock_force_cancel.await_args.kwargs["node_id"] == node_id


async def test_terminate_failed_deploy_keeps_node_with_other_live_deploy(app_and_pool):
    """C9 guard: a FAILED deploy must NOT destroy the node if another live
    deploy still targets it."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool, provider="aws")
    node_id = await _seed_ready_node(pool, pool_id, gpu_total=4, gpu_allocated=1)
    org = str(__import__("uuid").uuid4())
    failed = await _seed_deploy(pool, pool_id, state="FAILED", gpu_per_replica=1,
                                target_node_id=node_id, org_id=org)
    await _seed_deploy(pool, pool_id, state="RUNNING", gpu_per_replica=1,
                       target_node_id=node_id, org_id=org)
    with patch(
        "inferia.services.orchestration.services.provisioning.jobs.repository."
        "ProvisioningJobRepository.force_cancel",
        new_callable=AsyncMock, return_value=True,
    ) as mock_force_cancel:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/deployment/terminate", json={"deployment_id": str(failed)}
            )
    assert resp.status_code == 200
    mock_force_cancel.assert_not_awaited()


async def test_terminate_running_clears_target_node_id(app_and_pool):
    """RUNNING/DEPLOYING path: target_node_id must be NULL after terminate
    (defense in depth — unbind called alongside release_gpu so a destroyed
    node is never referenced by a TERMINATED deploy)."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)
    node_id = await _seed_ready_node(pool, pool_id, gpu_total=4, gpu_allocated=1)
    deploy_id = await _seed_deploy(
        pool, pool_id,
        state="RUNNING",
        gpu_per_replica=1,
        target_node_id=node_id,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/deployment/terminate", json={"deployment_id": str(deploy_id)}
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "TERMINATED"

    async with pool.acquire() as c:
        deploy_row = await c.fetchrow(
            "SELECT state, target_node_id FROM model_deployments WHERE deployment_id=$1",
            deploy_id,
        )
    assert deploy_row["state"] == "TERMINATED"
    assert deploy_row["target_node_id"] is None, (
        f"expected target_node_id=NULL after RUNNING terminate, got {deploy_row['target_node_id']}"
    )


async def test_terminate_deploying_clears_target_node_id(app_and_pool):
    """DEPLOYING state path: target_node_id must also be NULL after terminate."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)
    node_id = await _seed_ready_node(pool, pool_id, gpu_total=4, gpu_allocated=1)
    deploy_id = await _seed_deploy(
        pool, pool_id,
        state="DEPLOYING",
        gpu_per_replica=1,
        target_node_id=node_id,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/deployment/terminate", json={"deployment_id": str(deploy_id)}
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "TERMINATED"

    async with pool.acquire() as c:
        deploy_row = await c.fetchrow(
            "SELECT state, target_node_id FROM model_deployments WHERE deployment_id=$1",
            deploy_id,
        )
    assert deploy_row["state"] == "TERMINATED"
    assert deploy_row["target_node_id"] is None, (
        f"expected target_node_id=NULL after DEPLOYING terminate, got {deploy_row['target_node_id']}"
    )
