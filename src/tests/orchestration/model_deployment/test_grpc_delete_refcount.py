"""Task 2.4 — unify the gRPC delete path onto the refcount-aware,
node-scoped teardown.

There are two delete-deployment entrypoints:

* REST ``POST /deployment/terminate`` (the CORRECT path) — release_gpu +
  ``_initiate_node_destroy`` -> ``force_cancel`` -> reconciler CancelHandler runs
  the node-scoped ``inferia-<node_id>`` ``pulumi destroy``.
* gRPC ``DeleteDeployment`` -> ``controller.request_delete`` ->
  ``model.terminate.requested`` -> ``worker.handle_terminate_requested``. The
  legacy handler called ``adapter.deprovision_node`` for AWS, destroying the
  WRONG (pool-scoped) stack with NO refcount release -> EC2 leak.

This module proves:

(a) ``terminate_deployment_core`` directly drives the refcount teardown against a
    real Postgres (last deploy on a node -> force_cancel fires; with another live
    deploy on the node -> no destroy).
(b) the gRPC ``handle_terminate_requested`` path for an AWS deploy routes through
    ``terminate_deployment_core`` (force_cancel) and does NOT call
    ``adapter.deprovision_node``.
(c) a non-reconciler provider (nosana) still uses the legacy ``deprovision_node``
    path — the legacy teardown is not regressed.

Run with:
    docker exec inferia-test sh -lc 'cd /usr/local/lib/python3.12/site-packages/\
inferia/services/orchestration/services/model_deployment/tests && \
python -m pytest test_grpc_delete_refcount.py -q'
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4, UUID

import asyncpg
import pytest
import pytest_asyncio

from services.orchestration.model_deployment import (
    deployment_server,
)
from services.orchestration.model_deployment.deployment_server import (
    _build_terminate_deps,
    terminate_deployment_core,
)
from services.orchestration.model_deployment.worker import (
    ModelDeploymentWorker,
)
from services.orchestration.repositories.inventory_repo import (
    InventoryRepository,
)
from services.orchestration.repositories.model_deployment_repo import (
    ModelDeploymentRepository,
)
from services.orchestration.repositories.pool_repo import (
    ComputePoolRepository,
)
from services.orchestration.repositories.placement_repo import (
    PlacementRepository,
)
from services.orchestration.worker_controller.controller import (
    WorkerController,
)

# Reuse the proven seed helpers from the REST terminate test module so part (a)
# exercises the exact same DB fixtures the REST path is verified against.
from tests.orchestration.model_deployment.test_terminate_endpoint_refcount import (
    _seed_pool,
    _seed_node,
    _seed_ready_node,
    _seed_deploy,
)

pytestmark = pytest.mark.asyncio

_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://inferia:inferia@localhost:5432/inferia_test",
)

_JOBS_REPO = (
    "services.orchestration.provisioning_state_machine.jobs.repository."
    "ProvisioningJobRepository"
)


@pytest_asyncio.fixture
async def db_pool():
    p = await asyncpg.create_pool(dsn=_DSN, min_size=1, max_size=4)
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# (a) terminate_deployment_core drives the refcount teardown directly
# ---------------------------------------------------------------------------


async def test_core_last_deploy_force_cancels_node(db_pool):
    """Last live deploy on a node -> core flags terminating + force_cancels the
    node's reconciler job (node-scoped destroy), NOT a fresh enqueue."""
    pool_id = await _seed_pool(db_pool, provider="aws")
    node_id = await _seed_ready_node(db_pool, pool_id, gpu_total=4, gpu_allocated=1)
    deploy_id = await _seed_deploy(
        db_pool, pool_id, state="RUNNING", gpu_per_replica=1, target_node_id=node_id,
    )

    deps = _build_terminate_deps(
        db_pool, controller=AsyncMock(spec=WorkerController), event_bus=None,
    )

    with patch(f"{_JOBS_REPO}.force_cancel", new_callable=AsyncMock,
               return_value=True) as mock_force_cancel, \
         patch(f"{_JOBS_REPO}.enqueue", new_callable=AsyncMock) as mock_enqueue:
        result = await terminate_deployment_core(deploy_id, deps=deps)

    assert result == {"deployment_id": str(deploy_id), "status": "TERMINATED"}
    mock_force_cancel.assert_awaited_once()
    assert mock_force_cancel.await_args.kwargs["node_id"] == node_id
    mock_enqueue.assert_not_awaited()

    async with db_pool.acquire() as c:
        state = await c.fetchval(
            "SELECT state FROM model_deployments WHERE deployment_id=$1", deploy_id,
        )
        terminating = await c.fetchval(
            "SELECT metadata->>'terminating' FROM compute_inventory WHERE id=$1",
            node_id,
        )
        gpu_alloc = await c.fetchval(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", node_id,
        )
    assert state == "TERMINATED"
    assert terminating == "true"
    assert gpu_alloc == 0


async def test_core_with_other_live_deploy_does_not_destroy(db_pool):
    """Another live deploy still references the node -> no force_cancel, the
    node (and its EC2) is preserved; only the terminated deploy's GPU is freed."""
    pool_id = await _seed_pool(db_pool, provider="aws")
    node_id = await _seed_ready_node(db_pool, pool_id, gpu_total=4, gpu_allocated=2)
    org_id = str(uuid4())
    deploy_a = await _seed_deploy(
        db_pool, pool_id, state="RUNNING", gpu_per_replica=1,
        target_node_id=node_id, org_id=org_id,
    )
    deploy_b = await _seed_deploy(
        db_pool, pool_id, state="RUNNING", gpu_per_replica=1,
        target_node_id=node_id, org_id=org_id,
    )

    deps = _build_terminate_deps(
        db_pool, controller=AsyncMock(spec=WorkerController), event_bus=None,
    )

    with patch(f"{_JOBS_REPO}.force_cancel", new_callable=AsyncMock,
               return_value=True) as mock_force_cancel, \
         patch(f"{_JOBS_REPO}.enqueue", new_callable=AsyncMock) as mock_enqueue:
        result = await terminate_deployment_core(deploy_a, deps=deps)

    assert result["status"] == "TERMINATED"
    mock_force_cancel.assert_not_awaited()
    mock_enqueue.assert_not_awaited()

    async with db_pool.acquire() as c:
        state_a = await c.fetchval(
            "SELECT state FROM model_deployments WHERE deployment_id=$1", deploy_a,
        )
        state_b = await c.fetchval(
            "SELECT state FROM model_deployments WHERE deployment_id=$1", deploy_b,
        )
        gpu_alloc = await c.fetchval(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", node_id,
        )
    assert state_a == "TERMINATED"
    assert state_b == "RUNNING"
    assert gpu_alloc == 1


async def test_core_not_found_raises_404(db_pool):
    """Unknown deployment_id -> HTTPException(404) (same as the REST route)."""
    from fastapi import HTTPException

    deps = _build_terminate_deps(db_pool, controller=None, event_bus=None)
    with pytest.raises(HTTPException) as ei:
        await terminate_deployment_core(uuid4(), deps=deps)
    assert ei.value.status_code == 404


# ---------------------------------------------------------------------------
# Worker construction helper for the gRPC-path tests
# ---------------------------------------------------------------------------


def _make_worker(*, db_pool, worker_controller=None):
    """Build a ModelDeploymentWorker whose repos hit the real test DB so the
    handler can drive terminate_deployment_core end-to-end."""
    deployment_repo = ModelDeploymentRepository(db_pool, event_bus=None)
    inventory_repo = InventoryRepository(db_pool)
    pool_repo = ComputePoolRepository(db_pool)
    placement_repo = PlacementRepository(db_pool)
    return ModelDeploymentWorker(
        deployment_repo=deployment_repo,
        model_registry_repo=AsyncMock(),
        pool_repo=pool_repo,
        placement_repo=placement_repo,
        scheduler=AsyncMock(),
        inventory_repo=inventory_repo,
        runtime_resolver=MagicMock(),
        runtime_strategies={},
        worker_controller=worker_controller,
    )


# ---------------------------------------------------------------------------
# (b) gRPC path: AWS deploy routes through the refcount teardown, never
#     adapter.deprovision_node
# ---------------------------------------------------------------------------


async def test_grpc_aws_terminate_routes_through_core_not_deprovision(db_pool):
    """handle_terminate_requested for an AWS (reconciler-managed) deploy must
    force_cancel the node's job (node-scoped destroy) and must NOT call the
    legacy adapter.deprovision_node (pool-scoped, leaks the EC2)."""
    pool_id = await _seed_pool(db_pool, provider="aws")
    node_id = await _seed_ready_node(db_pool, pool_id, gpu_total=4, gpu_allocated=1)
    # gRPC request_delete flips the row to TERMINATING before this handler runs.
    deploy_id = await _seed_deploy(
        db_pool, pool_id, state="TERMINATING", gpu_per_replica=1,
        target_node_id=node_id,
    )

    controller = AsyncMock(spec=WorkerController)
    worker = _make_worker(db_pool=db_pool, worker_controller=controller)

    # Adapter pinned to the real interface so a stray deprovision_node call is a
    # genuine signature-checked AsyncMock we can assert was NOT awaited.
    from providers.nosana.nosana_adapter import (
        NosanaAdapter,
    )
    # spec=NosanaAdapter is a concrete adapter that DEFINES deprovision_node, so
    # the .assert_not_awaited() below is signature-checked (MEMORY: AsyncMock
    # signature blindness) — proving the legacy pool-scoped path is unreachable.
    mock_adapter = AsyncMock(spec=NosanaAdapter)

    with patch(f"{_JOBS_REPO}.force_cancel", new_callable=AsyncMock,
               return_value=True) as mock_force_cancel, \
         patch(f"{_JOBS_REPO}.enqueue", new_callable=AsyncMock) as mock_enqueue, \
         patch(
             "services.orchestration.model_deployment.worker.get_adapter",
             return_value=mock_adapter,
         ):
        await worker.handle_terminate_requested(deploy_id)

    # The node-scoped destroy fired via force_cancel — and the legacy
    # pool-scoped deprovision_node was never touched.
    mock_force_cancel.assert_awaited_once()
    assert mock_force_cancel.await_args.kwargs["node_id"] == node_id
    mock_enqueue.assert_not_awaited()
    mock_adapter.deprovision_node.assert_not_awaited()
    # The shared core unloaded the model over the WS channel before destroy.
    controller.unload_model.assert_awaited_once()

    async with db_pool.acquire() as c:
        state = await c.fetchval(
            "SELECT state FROM model_deployments WHERE deployment_id=$1", deploy_id,
        )
        terminating = await c.fetchval(
            "SELECT metadata->>'terminating' FROM compute_inventory WHERE id=$1",
            node_id,
        )
    assert state == "TERMINATED"
    assert terminating == "true"


async def test_grpc_aws_terminate_with_other_deploy_keeps_node(db_pool):
    """gRPC AWS terminate with a second live deploy on the node: refcount > 0,
    so neither force_cancel nor deprovision_node fires; the node is kept."""
    pool_id = await _seed_pool(db_pool, provider="aws")
    node_id = await _seed_ready_node(db_pool, pool_id, gpu_total=4, gpu_allocated=2)
    org_id = str(uuid4())
    deploy_a = await _seed_deploy(
        db_pool, pool_id, state="TERMINATING", gpu_per_replica=1,
        target_node_id=node_id, org_id=org_id,
    )
    deploy_b = await _seed_deploy(
        db_pool, pool_id, state="RUNNING", gpu_per_replica=1,
        target_node_id=node_id, org_id=org_id,
    )

    controller = AsyncMock(spec=WorkerController)
    worker = _make_worker(db_pool=db_pool, worker_controller=controller)

    from providers.nosana.nosana_adapter import (
        NosanaAdapter,
    )
    # spec=NosanaAdapter is a concrete adapter that DEFINES deprovision_node, so
    # the .assert_not_awaited() below is signature-checked (MEMORY: AsyncMock
    # signature blindness) — proving the legacy pool-scoped path is unreachable.
    mock_adapter = AsyncMock(spec=NosanaAdapter)

    with patch(f"{_JOBS_REPO}.force_cancel", new_callable=AsyncMock,
               return_value=True) as mock_force_cancel, \
         patch(f"{_JOBS_REPO}.enqueue", new_callable=AsyncMock), \
         patch(
             "services.orchestration.model_deployment.worker.get_adapter",
             return_value=mock_adapter,
         ):
        await worker.handle_terminate_requested(deploy_a)

    mock_force_cancel.assert_not_awaited()
    mock_adapter.deprovision_node.assert_not_awaited()

    async with db_pool.acquire() as c:
        state_a = await c.fetchval(
            "SELECT state FROM model_deployments WHERE deployment_id=$1", deploy_a,
        )
        state_b = await c.fetchval(
            "SELECT state FROM model_deployments WHERE deployment_id=$1", deploy_b,
        )
        gpu_alloc = await c.fetchval(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", node_id,
        )
    assert state_a == "TERMINATED"
    assert state_b == "RUNNING"
    assert gpu_alloc == 1


# NOTE: the 'worker' provider is in _RECONCILER_MANAGED_PROVIDERS but is NOT a
# valid compute_pools.provider enum value (provider_type = aws/gcp/azure/
# skypilot/nosana/akash/on_prem/other), so it can't be seeded as a pool here.
# Its routing is covered by the shared frozenset; we DB-seed the enum-valid
# reconciler providers.
@pytest.mark.parametrize("provider", ["aws", "gcp", "azure", "on_prem"])
async def test_grpc_reconciler_providers_never_deprovision(db_pool, provider):
    """Every reconciler-managed provider routes through the core (force_cancel)
    and never the legacy deprovision_node."""
    pool_id = await _seed_pool(db_pool, provider=provider)
    node_id = await _seed_ready_node(db_pool, pool_id, gpu_total=4, gpu_allocated=1)
    deploy_id = await _seed_deploy(
        db_pool, pool_id, state="TERMINATING", gpu_per_replica=1,
        target_node_id=node_id,
    )

    worker = _make_worker(
        db_pool=db_pool, worker_controller=AsyncMock(spec=WorkerController),
    )

    from providers.nosana.nosana_adapter import (
        NosanaAdapter,
    )
    # spec=NosanaAdapter is a concrete adapter that DEFINES deprovision_node, so
    # the .assert_not_awaited() below is signature-checked (MEMORY: AsyncMock
    # signature blindness) — proving the legacy pool-scoped path is unreachable.
    mock_adapter = AsyncMock(spec=NosanaAdapter)

    with patch(f"{_JOBS_REPO}.force_cancel", new_callable=AsyncMock,
               return_value=True) as mock_force_cancel, \
         patch(f"{_JOBS_REPO}.enqueue", new_callable=AsyncMock), \
         patch(
             "services.orchestration.model_deployment.worker.get_adapter",
             return_value=mock_adapter,
         ):
        await worker.handle_terminate_requested(deploy_id)

    mock_force_cancel.assert_awaited_once()
    mock_adapter.deprovision_node.assert_not_awaited()


# ---------------------------------------------------------------------------
# (c) Non-reconciler provider (nosana) still uses the legacy path
# ---------------------------------------------------------------------------


async def test_grpc_nosana_terminate_uses_legacy_deprovision(db_pool):
    """A nosana deploy (NOT reconciler-managed) must still go through the legacy
    adapter.deprovision_node teardown and must NOT touch force_cancel."""
    pool_id = await _seed_pool(db_pool, provider="nosana")
    node_id = await _seed_ready_node(db_pool, pool_id, gpu_total=4, gpu_allocated=1)
    deploy_id = await _seed_deploy(
        db_pool, pool_id, state="TERMINATING", gpu_per_replica=1,
        target_node_id=node_id,
    )

    worker = _make_worker(db_pool=db_pool, worker_controller=AsyncMock(spec=WorkerController))
    # The legacy path reads node_ids off the deployment row to find instances.
    worker.deployments.get = AsyncMock(return_value={
        "state": "TERMINATING",
        "deployment_id": str(deploy_id),
        "pool_id": str(pool_id),
        "node_ids": [node_id],
        "allocation_ids": [],
    })

    node_data = {
        "provider": "nosana",
        "provider_instance_id": "inst-1",
        "metadata": {},
    }
    worker.inventory.get_node_by_id = AsyncMock(return_value=node_data)
    worker.inventory.mark_terminated = AsyncMock()
    worker.inventory.recycle_node = AsyncMock()

    mock_adapter = MagicMock()
    mock_adapter.deprovision_node = AsyncMock()
    mock_adapter.get_capabilities.return_value = MagicMock(
        is_ephemeral=True, supports_cluster_mode=False,
    )

    with patch(f"{_JOBS_REPO}.force_cancel", new_callable=AsyncMock,
               return_value=True) as mock_force_cancel, \
         patch(
             "services.orchestration.model_deployment.worker.get_adapter",
             return_value=mock_adapter,
         ):
        await worker.handle_terminate_requested(deploy_id)

    # Legacy teardown ran; reconciler force_cancel did NOT.
    mock_adapter.deprovision_node.assert_awaited_once()
    mock_force_cancel.assert_not_awaited()
    # The legacy path's final state transition (real repo) lands the deploy in
    # STOPPED — not the core's TERMINATED — confirming it took the legacy branch.
    async with db_pool.acquire() as c:
        state = await c.fetchval(
            "SELECT state FROM model_deployments WHERE deployment_id=$1", deploy_id,
        )
    assert state == "STOPPED"


async def test_grpc_terminate_skips_when_not_terminating(db_pool):
    """The handler is a no-op when the row is not in TERMINATING — it does not
    route to the core or the legacy path."""
    pool_id = await _seed_pool(db_pool, provider="aws")
    node_id = await _seed_ready_node(db_pool, pool_id, gpu_total=4, gpu_allocated=1)
    deploy_id = await _seed_deploy(
        db_pool, pool_id, state="RUNNING", gpu_per_replica=1, target_node_id=node_id,
    )

    worker = _make_worker(db_pool=db_pool, worker_controller=AsyncMock(spec=WorkerController))

    with patch(f"{_JOBS_REPO}.force_cancel", new_callable=AsyncMock,
               return_value=True) as mock_force_cancel:
        await worker.handle_terminate_requested(deploy_id)

    mock_force_cancel.assert_not_awaited()
    async with db_pool.acquire() as c:
        state = await c.fetchval(
            "SELECT state FROM model_deployments WHERE deployment_id=$1", deploy_id,
        )
    # Untouched.
    assert state == "RUNNING"
