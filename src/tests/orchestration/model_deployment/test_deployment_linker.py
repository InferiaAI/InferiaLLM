from __future__ import annotations
import os
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
import asyncpg
from uuid import uuid4

from orchestration.models.model_deployment.deployment_linker import (
    DeploymentLinker,
)
from orchestration.repositories.inventory_repo import (
    InventoryRepository,
)
from orchestration.repositories.model_deployment_repo import (
    ModelDeploymentRepository,
)
from orchestration.workers.worker_controller.controller import (
    WorkerController,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def pool():
    dsn = os.getenv("TEST_DATABASE_URL",
                    "postgresql://inferia:inferia@localhost:5432/inferia_test")
    p = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
    yield p
    await p.close()


async def _seed_pool_and_node(p, *, gpu_total=4, gpu_allocated=0, state="ready",
                              advertise_url=None):
    org_id, pool_id, node_id = uuid4(), uuid4(), uuid4()
    async with p.acquire() as c:
        await c.execute("INSERT INTO organizations(id,name) VALUES($1,$2) "
                         "ON CONFLICT DO NOTHING", str(org_id), f"o-{org_id}")
        await c.execute(
            """INSERT INTO compute_pools(id, pool_name, owner_type, owner_id,
                 provider, pool_type, allowed_gpu_types, max_cost_per_hour,
                 scheduling_policy, provider_pool_id, is_active, lifecycle_state,
                 gpu_count)
               VALUES ($1, $2, 'organization', $3::text, 'aws', 'cluster',
                       ARRAY['none'], 0, '{}', $4, true, 'running', $5)""",
            pool_id, f"p-{pool_id}", str(org_id),
            f"placeholder:{pool_id}", gpu_total,
        )
        await c.execute(
            """INSERT INTO compute_inventory(id, pool_id, provider,
                 provider_instance_id, hostname, node_name, agent_kind,
                 advertise_url,
                 gpu_total, gpu_allocated, vcpu_total, vcpu_allocated,
                 ram_gb_total, ram_gb_allocated, state)
               VALUES ($1, $2, 'aws', $3, 'h', $4, 'worker',
                       $8,
                       $5, $6, 0, 0, 0, 0, $7)""",
            node_id, pool_id, f"p-{node_id}", f"n-{node_id}",
            gpu_total, gpu_allocated, state, advertise_url,
        )
    return pool_id, node_id


async def _seed_pending_deploy(p, pool_id, *, gpu_required=1, model_name="test-model"):
    deploy_id = uuid4()
    async with p.acquire() as c:
        await c.execute(
            """INSERT INTO model_deployments(deployment_id, model_name,
                 replicas, gpu_per_replica, pool_id,
                 target_pool_id, target_node_id, state, org_id)
               VALUES ($1, $5, 1, $4, $2, $2, NULL,
                       'PENDING_NODE', $3)""",
            deploy_id, pool_id, str(uuid4()), gpu_required, model_name,
        )
    return deploy_id


async def test_one_pending_deploy_binds_on_worker_ready(pool):
    pool_id, node_id = await _seed_pool_and_node(pool)
    deploy_id = await _seed_pending_deploy(pool, pool_id)
    controller = AsyncMock(spec=WorkerController)
    linker = DeploymentLinker(
        db_pool=pool,
        inventory_repo=InventoryRepository(pool),
        deployment_repo=ModelDeploymentRepository(pool, event_bus=None),
        worker_controller=controller,
    )

    await linker.on_worker_ready(node_id)

    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT state, target_node_id FROM model_deployments "
            "WHERE deployment_id=$1",
            deploy_id,
        )
    # load_model succeeded (AsyncMock, no side_effect) → the linker promotes
    # the deploy DEPLOYING → RUNNING so the dashboard reflects the live model.
    assert row["state"] == "RUNNING"
    assert row["target_node_id"] == node_id
    controller.load_model.assert_awaited_once()
    call_kwargs = controller.load_model.await_args.kwargs
    assert "spec" in call_kwargs
    assert call_kwargs["spec"]["deployment_id"]  # non-empty
    assert call_kwargs["spec"]["model"]["artifact_uri"]  # non-empty


async def test_on_worker_ready_sets_endpoint_to_node_advertise_url(pool):
    """After a successful load, the deploy's endpoint must be set to the
    node's CP-reachable advertise_url (the worker's :8080 inference proxy) so
    the inference data plane can route to it. The worker's own reported
    endpoint_url is a useless 127.0.0.1 loopback — we must use advertise_url.
    Without this the deployment has endpoint='' and the sandbox 'never
    connects to the node'."""
    pool_id, node_id = await _seed_pool_and_node(
        pool, advertise_url="http://ec2-test-host:8080",
    )
    deploy_id = await _seed_pending_deploy(pool, pool_id)
    controller = AsyncMock(spec=WorkerController)
    linker = DeploymentLinker(
        db_pool=pool,
        inventory_repo=InventoryRepository(pool),
        deployment_repo=ModelDeploymentRepository(pool, event_bus=None),
        worker_controller=controller,
    )

    await linker.on_worker_ready(node_id)

    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT state, endpoint FROM model_deployments WHERE deployment_id=$1",
            deploy_id,
        )
    assert row["state"] == "RUNNING"
    assert row["endpoint"] == "http://ec2-test-host:8080"


async def test_five_pending_with_capacity_three_binds_three_fifo(pool):
    pool_id, node_id = await _seed_pool_and_node(pool, gpu_total=3)
    deploys = [await _seed_pending_deploy(pool, pool_id) for _ in range(5)]
    controller = AsyncMock(spec=WorkerController)
    linker = DeploymentLinker(
        db_pool=pool,
        inventory_repo=InventoryRepository(pool),
        deployment_repo=ModelDeploymentRepository(pool, event_bus=None),
        worker_controller=controller,
    )

    await linker.on_worker_ready(node_id)

    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT deployment_id, state FROM model_deployments "
            "WHERE deployment_id = ANY($1::uuid[]) "
            "ORDER BY created_at ASC",
            deploys,
        )
    states = [r["state"] for r in rows]
    # The 3 that fit load successfully → RUNNING; the 2 over capacity stay
    # PENDING_NODE.
    assert states == ["RUNNING", "RUNNING", "RUNNING",
                       "PENDING_NODE", "PENDING_NODE"]
    assert controller.load_model.await_count == 3


async def test_no_pending_deploys_is_noop(pool):
    _, node_id = await _seed_pool_and_node(pool)
    controller = AsyncMock(spec=WorkerController)
    linker = DeploymentLinker(
        db_pool=pool,
        inventory_repo=InventoryRepository(pool),
        deployment_repo=ModelDeploymentRepository(pool, event_bus=None),
        worker_controller=controller,
    )

    await linker.on_worker_ready(node_id)
    controller.load_model.assert_not_called()


async def test_pending_deploy_in_other_pool_ignored(pool):
    pool_a, node_a = await _seed_pool_and_node(pool)
    pool_b, _ = await _seed_pool_and_node(pool)
    await _seed_pending_deploy(pool, pool_b)
    controller = AsyncMock(spec=WorkerController)
    linker = DeploymentLinker(
        db_pool=pool,
        inventory_repo=InventoryRepository(pool),
        deployment_repo=ModelDeploymentRepository(pool, event_bus=None),
        worker_controller=controller,
    )

    await linker.on_worker_ready(node_a)

    controller.load_model.assert_not_called()


async def test_load_model_failure_releases_gpu_and_marks_failed(pool):
    pool_id, node_id = await _seed_pool_and_node(pool, gpu_total=2)
    deploy_id = await _seed_pending_deploy(pool, pool_id)
    controller = AsyncMock(spec=WorkerController)
    controller.load_model.side_effect = RuntimeError("worker offline")
    linker = DeploymentLinker(
        db_pool=pool,
        inventory_repo=InventoryRepository(pool),
        deployment_repo=ModelDeploymentRepository(pool, event_bus=None),
        worker_controller=controller,
    )

    await linker.on_worker_ready(node_id)

    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT state FROM model_deployments WHERE deployment_id=$1",
            deploy_id,
        )
        node_row = await c.fetchrow(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1",
            node_id,
        )
    assert row["state"] == "FAILED"
    assert node_row["gpu_allocated"] == 0  # released after load_model failure


async def test_load_model_failed_status_releases_gpu_and_marks_failed(pool):
    """The worker can return a CommandResult with status='failed' (e.g. the
    readiness probe timed out) WITHOUT raising — controller.load_model returns
    that body verbatim. The linker must treat it as a load failure (release
    GPU + FAILED + NO endpoint), not silently mark the deploy RUNNING."""
    from orchestration.workers.worker_controller.protocol import (
        CommandResultBody,
    )
    pool_id, node_id = await _seed_pool_and_node(
        pool, gpu_total=2, advertise_url="http://ec2-x:8080",
    )
    deploy_id = await _seed_pending_deploy(pool, pool_id)
    controller = AsyncMock(spec=WorkerController)
    controller.load_model = AsyncMock(return_value=CommandResultBody(
        in_reply_to="x", status="failed", detail="readiness probe timed out",
    ))
    linker = DeploymentLinker(
        db_pool=pool,
        inventory_repo=InventoryRepository(pool),
        deployment_repo=ModelDeploymentRepository(pool, event_bus=None),
        worker_controller=controller,
    )

    await linker.on_worker_ready(node_id)

    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT state, endpoint FROM model_deployments WHERE deployment_id=$1",
            deploy_id,
        )
        node_row = await c.fetchrow(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", node_id,
        )
    assert row["state"] == "FAILED"          # NOT RUNNING
    assert not row["endpoint"]               # endpoint NOT published on failure
    assert node_row["gpu_allocated"] == 0    # GPU released


async def test_already_bound_deploy_is_promoted_without_reallocate(pool):
    """A ColdStart deploy pre-allocates its GPU on the placeholder and is
    bound to it (target_node_id=node). When the worker registers onto that
    same row, the linker must PROMOTE it (PENDING_NODE->DEPLOYING) without
    re-allocating — re-allocating would double-count and fail on the now-full
    node, stranding the deploy in PENDING_NODE forever (the live bug)."""
    pool_id, node_id = await _seed_pool_and_node(
        pool, gpu_total=1, gpu_allocated=1,  # full: GPU already reserved
    )
    # Deploy already bound to this node (as ColdStart leaves it).
    deploy_id = uuid4()
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO model_deployments(deployment_id, model_name,
                 replicas, gpu_per_replica, pool_id, target_pool_id,
                 target_node_id, state, org_id)
               VALUES ($1, 'm', 1, 1, $2, $2, $3, 'PENDING_NODE', $4)""",
            deploy_id, pool_id, node_id, str(uuid4()),
        )
    controller = AsyncMock(spec=WorkerController)
    linker = DeploymentLinker(
        db_pool=pool,
        inventory_repo=InventoryRepository(pool),
        deployment_repo=ModelDeploymentRepository(pool, event_bus=None),
        worker_controller=controller,
    )

    await linker.on_worker_ready(node_id)

    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT state FROM model_deployments WHERE deployment_id=$1",
            deploy_id,
        )
        node = await c.fetchrow(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", node_id,
        )
    # Promoted (not stranded) AND load succeeded → RUNNING; GPU not
    # double-allocated.
    assert row["state"] == "RUNNING"
    assert node["gpu_allocated"] == 1            # NOT double-allocated
    controller.load_model.assert_awaited_once()  # model load fired
