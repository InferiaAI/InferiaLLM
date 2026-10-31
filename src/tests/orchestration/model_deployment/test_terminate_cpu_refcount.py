"""Terminating a CPU-only (0-GPU) deployment must still tear its node down.

Regression for a leak found in session-3's live llm-d run (2026-10-31): the
terminate refcount released the node only via ``release_gpu``, and that call
was gated on ``gpu_per_replica > 0``. A CPU deployment on a direct-adapter pool
(k8s llm-d, or plain k8s on CPU) therefore never set ``should_destroy``; the
API answered TERMINATED/DELETED while every Kubernetes object stayed up.

``release_gpu(node, 0)`` is the right call for those: it decrements nothing and
destroys only when the node has no GPUs allocated AND no other
PENDING_NODE/DEPLOYING/RUNNING deployment targets it (one CTE, one snapshot).

Needs a migrated Postgres, like the other refcount tests:
    TEST_DATABASE_URL=postgresql://... pytest <this file>
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from orchestration.models.model_deployment import deployment_server
from orchestration.models.model_deployment.deployment_server import (
    _build_terminate_deps,
    terminate_deployment_core,
)
from orchestration.workers.worker_controller.controller import WorkerController
from tests.orchestration.model_deployment.test_terminate_endpoint_refcount import (
    _seed_deploy,
    _seed_pool,
    _seed_ready_node,
)

_DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _DSN, reason="needs TEST_DATABASE_URL"),
]


@pytest_asyncio.fixture
async def db_pool():
    p = await asyncpg.create_pool(dsn=_DSN, min_size=1, max_size=4)
    yield p
    await p.close()


async def _terminate(db_pool, deploy_id):
    deps = _build_terminate_deps(
        db_pool, controller=AsyncMock(spec=WorkerController), event_bus=None,
    )
    with patch.object(deployment_server, "_deprovision_direct_node",
                      AsyncMock(return_value=(True, None))) as deprov:
        result = await terminate_deployment_core(deploy_id, deps=deps)
    return result, deprov


async def _node_state(db_pool, node_id):
    async with db_pool.acquire() as c:
        return await c.fetchval("SELECT state FROM compute_inventory WHERE id=$1", node_id)


@pytest.mark.parametrize("state", ["RUNNING", "PENDING_NODE"])
async def test_last_cpu_deploy_destroys_its_direct_node(db_pool, state):
    pool_id = await _seed_pool(db_pool, provider="k8s")
    node_id = await _seed_ready_node(db_pool, pool_id, gpu_total=0, gpu_allocated=0)
    deploy_id = await _seed_deploy(
        db_pool, pool_id, state=state, gpu_per_replica=0, target_node_id=node_id,
    )
    result, deprov = await _terminate(db_pool, deploy_id)
    assert result["status"] == "TERMINATED"
    deprov.assert_awaited_once()
    assert await _node_state(db_pool, node_id) == "terminated"


async def test_cpu_deploy_keeps_a_node_another_deploy_still_uses(db_pool):
    pool_id = await _seed_pool(db_pool, provider="k8s")
    node_id = await _seed_ready_node(db_pool, pool_id, gpu_total=0, gpu_allocated=0)
    org_id = str(uuid4())
    a = await _seed_deploy(db_pool, pool_id, state="RUNNING", gpu_per_replica=0,
                           target_node_id=node_id, org_id=org_id)
    b = await _seed_deploy(db_pool, pool_id, state="RUNNING", gpu_per_replica=0,
                           target_node_id=node_id, org_id=org_id)
    result, deprov = await _terminate(db_pool, a)
    assert result["status"] == "TERMINATED"
    deprov.assert_not_awaited()
    assert await _node_state(db_pool, node_id) != "terminated"
    async with db_pool.acquire() as c:
        assert await c.fetchval(
            "SELECT state FROM model_deployments WHERE deployment_id=$1", b) == "RUNNING"


async def test_cpu_deploy_keeps_a_node_with_gpus_still_allocated(db_pool):
    pool_id = await _seed_pool(db_pool, provider="k8s")
    node_id = await _seed_ready_node(db_pool, pool_id, gpu_total=4, gpu_allocated=1)
    deploy_id = await _seed_deploy(db_pool, pool_id, state="RUNNING", gpu_per_replica=0,
                                   target_node_id=node_id)
    _, deprov = await _terminate(db_pool, deploy_id)
    deprov.assert_not_awaited()
    async with db_pool.acquire() as c:
        assert await c.fetchval(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", node_id) == 1
