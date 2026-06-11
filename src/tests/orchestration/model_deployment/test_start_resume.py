"""Integration tests for the resume (``POST /deployment/start``) endpoint.

The resume path must run the SAME place+provision core as ``/deploy`` so a
paused/terminated/failed deployment is re-placed onto a freshly provisioned
node — NOT forwarded to the legacy ``model.deploy.requested`` worker (which
calls ``adapter.provision_node`` and raises ``NotImplementedError`` for AWS,
sending the resumed deploy straight to FAILED).

These tests spin up an isolated FastAPI app with the deployment_server router,
set ``app.state.pool`` to a real asyncpg pool against ``inferia_test``, mock
``app.state.worker_controller`` and install a SPY ``event_bus`` so we can assert
the legacy ``model.deploy.requested`` topic is never published on resume.

The real DB makes the transactional ``PoolPlacer.place`` + bind + state
transitions run for real, so the assertions key off committed DB state (a fresh
placeholder node, a cleared-then-rebound ``target_node_id``) plus the spy.

Run with:
    docker exec inferia-test sh -lc 'cd /usr/local/lib/python3.12/\
site-packages/inferia/services/orchestration/services/model_deployment/tests \
&& python -m pytest test_start_resume.py -q'
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4, UUID

import asyncpg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from orchestration.models.model_deployment import (
    deployment_server,
)
from orchestration.workers.worker_controller.controller import (
    WorkerController,
)

pytestmark = pytest.mark.asyncio

_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://inferia:inferia@localhost:5432/inferia_test",
)


# ---------------------------------------------------------------------------
# Spy event bus — records every publish so we can assert the legacy
# model.deploy.requested topic is NEVER fired on resume.
# ---------------------------------------------------------------------------
class _SpyEventBus:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    async def publish(self, topic, payload=None):
        self.published.append((topic, payload))

    def topics(self) -> list[str]:
        return [t for (t, _) in self.published]


@pytest_asyncio.fixture
async def db_pool():
    p = await asyncpg.create_pool(dsn=_DSN, min_size=1, max_size=4)
    yield p
    await p.close()


@pytest_asyncio.fixture
async def app_and_pool(db_pool):
    app = FastAPI()
    app.state.pool = db_pool
    app.state.worker_controller = AsyncMock(spec=WorkerController)
    app.state.event_bus = _SpyEventBus()
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
    instance_type: str = "g6.xlarge",
    region: str = "us-east-1",
) -> tuple[UUID, str]:
    """Insert a compute_pool row; return (pool_id, org_id)."""
    org_id = uuid4()
    pool_id = uuid4()
    meta_json = json.dumps(metadata or {})
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO organizations(id, name) VALUES($1, $2) "
            "ON CONFLICT DO NOTHING",
            str(org_id), f"o-{org_id}",
        )
        await c.execute(
            """INSERT INTO compute_pools(
                   id, pool_name, owner_type, owner_id, provider, pool_type,
                   allowed_gpu_types, max_cost_per_hour, scheduling_policy,
                   provider_pool_id, is_active, lifecycle_state, gpu_count,
                   max_nodes, metadata, region_constraint
               )
               VALUES ($1, $2, 'organization', $3::text, $4, 'cluster',
                       ARRAY[$10], 0, '{}', $5, true, $6, $7, $8, $9::jsonb,
                       ARRAY[$11])""",
            pool_id, f"p-{pool_id}", str(org_id), provider,
            f"placeholder:{pool_id}", lifecycle_state, gpu_count, max_nodes,
            meta_json, instance_type, region,
        )
    return pool_id, str(org_id)


async def _seed_node(
    pool,
    pool_id: UUID,
    *,
    gpu_total: int = 4,
    gpu_allocated: int = 0,
    state: str = "ready",
) -> UUID:
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


async def _seed_deploy(
    pool,
    *,
    pool_id: UUID,
    org_id: str,
    state: str,
    target_node_id: UUID | None = None,
    gpu_per_replica: int = 1,
    engine: str = "vllm",
    configuration: dict | None = None,
    model_name: str = "resume-model",
) -> UUID:
    """Insert a model_deployments row, return its deployment_id."""
    deploy_id = uuid4()
    cfg = configuration if configuration is not None else {
        "model": {"artifact_uri": "hf://resume-model"},
    }
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO model_deployments(
                   deployment_id, model_id, model_name, engine, configuration,
                   pool_id, replicas, gpu_per_replica, state, org_id,
                   target_pool_id, target_node_id
               )
               VALUES ($1, NULL, $2, $3, $4::jsonb, $5, 1, $6, $7, $8,
                       $5, $9)""",
            deploy_id, model_name, engine, json.dumps(cfg), pool_id,
            gpu_per_replica, state, org_id, target_node_id,
        )
    return deploy_id


async def _get_deploy(pool, deploy_id: UUID) -> dict:
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT state, target_node_id, target_pool_id "
            "FROM model_deployments WHERE deployment_id=$1",
            deploy_id,
        )
    return dict(row)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
async def test_resume_terminated_reprovisions_clears_stale_binding(app_and_pool):
    """Resume of a TERMINATED deploy whose ``target_node_id`` points at a
    now-destroyed node:

    - clears that stale binding (unbind),
    - re-runs PoolPlacer.place onto the pool (an empty pool => ColdStart =>
      a fresh placeholder node + a provisioning job),
    - re-binds to the NEW node and lands in PENDING_NODE/DEPLOYING,
    - NEVER publishes the legacy ``model.deploy.requested`` topic.
    """
    app, pool = app_and_pool
    pool_id, org_id = await _seed_pool(pool)
    # Stale node the old deploy was bound to. The real-world stale node was
    # destroyed; we model it as a node in a SEPARATE pool so PoolPlacer for the
    # deploy's (empty) pool can never re-place onto it (target_node_id has an FK
    # to compute_inventory, so it must be a real row).
    other_pool_id, _ = await _seed_pool(pool)
    stale_node = await _seed_node(
        pool, other_pool_id, gpu_total=4, gpu_allocated=1, state="ready",
    )
    deploy_id = await _seed_deploy(
        pool, pool_id=pool_id, org_id=org_id, state="TERMINATED",
        target_node_id=stale_node,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/deployment/start", json={"deployment_id": str(deploy_id)},
        )

    assert resp.status_code in (200, 202), resp.text
    body = resp.json()
    assert body["state"] in ("PENDING_NODE", "DEPLOYING")

    # The new target_node_id must NOT be the stale node — a fresh placeholder
    # was created by ColdStart.
    new_node = UUID(body["target_node_id"])
    assert new_node != stale_node

    row = await _get_deploy(pool, deploy_id)
    assert row["state"] in ("PENDING_NODE", "DEPLOYING")
    assert row["target_node_id"] == new_node

    # The fresh node is a provisioning placeholder in this pool.
    async with pool.acquire() as c:
        node_state = await c.fetchval(
            "SELECT state FROM compute_inventory WHERE id=$1", new_node,
        )
        assert node_state == "provisioning"
        job_count = await c.fetchval(
            "SELECT COUNT(*) FROM provisioning_jobs WHERE pool_id=$1", pool_id,
        )
        assert job_count == 1, "resume must enqueue exactly one ProvisioningJob"

    # Legacy worker event must never fire on resume.
    assert "model.deploy.requested" not in app.state.event_bus.topics()


async def test_resume_warm_pool_binds_to_ready_node(app_and_pool):
    """Resume onto a pool that already has a ready node with capacity:
    BindToReady warm path => load_model (status=ok) => RUNNING, no
    provisioning job, no legacy event. Previously a successful warm load
    stayed DEPLOYING forever; it now promotes to RUNNING."""
    app, pool = app_and_pool
    pool_id, org_id = await _seed_pool(pool)
    ready_node = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=0)
    # Stale binding lives in a separate pool (destroyed-node stand-in).
    other_pool_id, _ = await _seed_pool(pool)
    stale_node = await _seed_node(
        pool, other_pool_id, gpu_total=4, gpu_allocated=1, state="ready",
    )
    deploy_id = await _seed_deploy(
        pool, pool_id=pool_id, org_id=org_id, state="STOPPED",
        target_node_id=stale_node, gpu_per_replica=2,
        configuration={"model": {"artifact_uri": "hf://resume-model"}},
    )

    from orchestration.workers.worker_controller.protocol import (
        CommandResultBody,
    )
    app.state.worker_controller.load_model.return_value = CommandResultBody(
        in_reply_to="x", status="ok", detail="",
        endpoint_url="http://127.0.0.1:9000",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/deployment/start", json={"deployment_id": str(deploy_id)},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "RUNNING"
    assert UUID(body["target_node_id"]) == ready_node

    row = await _get_deploy(pool, deploy_id)
    assert row["target_node_id"] == ready_node

    # Warm path: GPU allocated on the ready node, load_model fired, no job.
    async with pool.acquire() as c:
        alloc = await c.fetchval(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", ready_node,
        )
        assert alloc == 2
        job_count = await c.fetchval(
            "SELECT COUNT(*) FROM provisioning_jobs WHERE pool_id=$1", pool_id,
        )
        assert job_count == 0

    app.state.worker_controller.load_model.assert_awaited_once()
    assert "model.deploy.requested" not in app.state.event_bus.topics()


async def test_resume_rejected_when_running(app_and_pool):
    """A RUNNING deployment cannot be resumed (422)."""
    app, pool = app_and_pool
    pool_id, org_id = await _seed_pool(pool)
    deploy_id = await _seed_deploy(
        pool, pool_id=pool_id, org_id=org_id, state="RUNNING",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/deployment/start", json={"deployment_id": str(deploy_id)},
        )

    assert resp.status_code == 422, resp.text
    assert "cannot start deployment in state" in resp.text.lower()
    # No placement / no legacy event for a rejected resume.
    assert "model.deploy.requested" not in app.state.event_bus.topics()


async def test_resume_deployment_missing_returns_404(app_and_pool):
    app, pool = app_and_pool
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/deployment/start", json={"deployment_id": str(uuid4())},
        )
    assert resp.status_code == 404, resp.text


async def test_resume_bad_uuid_returns_400(app_and_pool):
    app, pool = app_and_pool
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/deployment/start", json={"deployment_id": "not-a-uuid"},
        )
    assert resp.status_code == 400, resp.text


async def test_resume_pool_missing_returns_404(app_and_pool):
    """The deploy's pool was deactivated/deleted out from under it =>
    ComputePoolRepository.get (WHERE is_active = TRUE) misses => 404."""
    app, pool = app_and_pool
    pool_id, org_id = await _seed_pool(pool)
    deploy_id = await _seed_deploy(
        pool, pool_id=pool_id, org_id=org_id, state="STOPPED",
    )
    # Deactivate the pool: the FK row still exists (so model_deployments.pool_id
    # stays valid) but ComputePoolRepository.get filters is_active=TRUE and now
    # returns nothing — exactly the "pool gone" lookup the route must 404 on.
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE compute_pools SET is_active = FALSE WHERE id = $1", pool_id,
        )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/deployment/start", json={"deployment_id": str(deploy_id)},
        )
    assert resp.status_code == 404, resp.text
    assert "pool" in resp.text.lower()


async def test_resume_terminating_pool_returns_409(app_and_pool):
    """Resuming onto a terminating pool must 409."""
    app, pool = app_and_pool
    pool_id, org_id = await _seed_pool(pool, lifecycle_state="terminating")
    deploy_id = await _seed_deploy(
        pool, pool_id=pool_id, org_id=org_id, state="STOPPED",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/deployment/start", json={"deployment_id": str(deploy_id)},
        )
    assert resp.status_code == 409, resp.text


async def test_resume_external_workload_runs_without_placement(app_and_pool):
    """An ``external`` workload (configuration.workload_type == 'external')
    short-circuits to RUNNING with no placement, no provisioning job and no
    legacy event."""
    app, pool = app_and_pool
    pool_id, org_id = await _seed_pool(pool)
    deploy_id = await _seed_deploy(
        pool, pool_id=pool_id, org_id=org_id, state="STOPPED",
        gpu_per_replica=0, engine="openai",
        configuration={"workload_type": "external"},
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/deployment/start", json={"deployment_id": str(deploy_id)},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "RUNNING"

    row = await _get_deploy(pool, deploy_id)
    assert row["state"] == "RUNNING"
    assert row["target_node_id"] is None  # no placement happened

    async with pool.acquire() as c:
        job_count = await c.fetchval(
            "SELECT COUNT(*) FROM provisioning_jobs WHERE pool_id=$1", pool_id,
        )
        assert job_count == 0
    app.state.worker_controller.load_model.assert_not_called()
    assert "model.deploy.requested" not in app.state.event_bus.topics()


# ---------------------------------------------------------------------------
# Unit-level: assert unbind + placer.place are invoked on the resume path with
# fully-mocked repos (AsyncMock(spec=...) per the AsyncMock-signature-blindness
# rule). This pins the contract the route relies on without a DB.
# ---------------------------------------------------------------------------
async def test_resume_impl_unbinds_and_places(monkeypatch):
    """``_start_deployment_impl`` clears the stale binding (unbind once) and
    drives PoolPlacer.place (>=1) before provisioning, returning a
    PENDING_NODE/DEPLOYING body — and never touches the legacy event bus."""
    from orchestration.models.model_deployment.pool_placer import (
        PoolPlacer, ColdStart,
    )
    from orchestration.repositories.inventory_repo import (
        InventoryRepository,
    )
    from orchestration.repositories.model_deployment_repo import (
        ModelDeploymentRepository,
    )
    from orchestration.repositories.pool_repo import (
        ComputePoolRepository,
    )
    from orchestration.state_machine.jobs.repository import (
        ProvisioningJobRepository,
    )

    deploy_id = uuid4()
    pool_id = uuid4()
    node_id = uuid4()
    org_id = str(uuid4())

    async def _fake_build_spec(*, pool_row, pool_meta, decision, org_id, ami_id=None):
        return {"provider": "aws", "instance_type": "g6.xlarge"}

    monkeypatch.setattr(
        deployment_server, "_build_provisioning_spec", _fake_build_spec
    )

    # --- transaction-capable conn / db_pool (mirror test_place_and_provision)
    class _TxCtx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    class _AcquireCtx:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *exc):
            return False

    conn = MagicMock(name="conn")
    conn.transaction = MagicMock(return_value=_TxCtx())
    db_pool = MagicMock(name="db_pool")
    db_pool.acquire = MagicMock(return_value=_AcquireCtx(conn))

    placer = AsyncMock(spec=PoolPlacer)
    placer.place.return_value = ColdStart(gpu_total_per_node=1, provider="aws")
    inventory = AsyncMock(spec=InventoryRepository)
    inventory.create_placeholder.return_value = node_id
    deploys = AsyncMock(spec=ModelDeploymentRepository)
    deploys.get.return_value = {
        "deployment_id": deploy_id,
        "state": "TERMINATED",
        "configuration": {"model": {"artifact_uri": "hf://m"}},
        "pool_id": pool_id,
        "target_pool_id": pool_id,
        "target_node_id": uuid4(),  # stale
        "gpu_per_replica": 1,
        "org_id": org_id,
        "engine": "vllm",
        "model_name": "m",
        "inference_model": None,
    }
    pool_repo = AsyncMock(spec=ComputePoolRepository)
    pool_repo.get.return_value = {
        "id": pool_id,
        "lifecycle_state": "running",
        "metadata": {},
        "allowed_gpu_types": ["g6.xlarge"],
        "region_constraint": ["us-east-1"],
        "provider_pool_id": "aws/g6.xlarge",
    }
    jobs_repo = AsyncMock(spec=ProvisioningJobRepository)
    controller = AsyncMock(spec=WorkerController)
    event_bus = _SpyEventBus()

    body, status = await deployment_server._start_deployment_impl(
        deployment_id=str(deploy_id),
        db_pool=db_pool,
        controller=controller,
        pool_repo=pool_repo,
        inventory=inventory,
        deploys=deploys,
        placer=placer,
        jobs_repo=jobs_repo,
    )

    assert status in (200, 202)
    assert body["state"] in ("PENDING_NODE", "DEPLOYING")

    # Stale binding cleared exactly once.
    deploys.unbind.assert_awaited_once()
    assert deploys.unbind.await_args.args[0] == deploy_id

    # Re-placement ran at least once.
    assert placer.place.await_count >= 1

    # Clean-slate state reset to CREATED happened before placement.
    set_states = [c.args[1] for c in deploys.set_state.await_args_list]
    assert "CREATED" in set_states

    # Legacy worker event never fired.
    assert "model.deploy.requested" not in event_bus.topics()


async def test_resume_reads_ami_id_from_configuration(monkeypatch):
    """_start_deployment_impl reads the persisted ami_id from the deployment
    row's configuration and passes it to place_and_provision (via
    _build_provisioning_spec's ami_id kwarg).

    Guards against AsyncMock-blindness: we capture the ami_id kwarg passed to
    _build_provisioning_spec (which place_and_provision calls internally) and
    assert it equals the value stored in configuration, not None.
    """
    from orchestration.models.model_deployment.pool_placer import (
        PoolPlacer, ColdStart,
    )
    from orchestration.repositories.inventory_repo import (
        InventoryRepository,
    )
    from orchestration.repositories.model_deployment_repo import (
        ModelDeploymentRepository,
    )
    from orchestration.repositories.pool_repo import (
        ComputePoolRepository,
    )
    from orchestration.state_machine.jobs.repository import (
        ProvisioningJobRepository,
    )

    deploy_id = uuid4()
    pool_id = uuid4()
    node_id = uuid4()
    org_id = str(uuid4())

    # Track ami_id values that reach _build_provisioning_spec
    captured_ami_ids: list[str | None] = []

    async def _spy_build_spec(*, pool_row, pool_meta, decision, org_id, ami_id=None):
        captured_ami_ids.append(ami_id)
        return {"provider": "aws", "instance_type": "g6.xlarge"}

    monkeypatch.setattr(
        deployment_server, "_build_provisioning_spec", _spy_build_spec
    )

    # --- transaction-capable conn / db_pool (mirrors test_resume_impl_unbinds_and_places)
    class _TxCtx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    class _AcquireCtx:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *exc):
            return False

    conn = MagicMock(name="conn")
    conn.transaction = MagicMock(return_value=_TxCtx())
    db_pool = MagicMock(name="db_pool")
    db_pool.acquire = MagicMock(return_value=_AcquireCtx(conn))

    placer = AsyncMock(spec=PoolPlacer)
    placer.place.return_value = ColdStart(gpu_total_per_node=1, provider="aws")
    inventory = AsyncMock(spec=InventoryRepository)
    inventory.create_placeholder.return_value = node_id
    deploys = AsyncMock(spec=ModelDeploymentRepository)

    # Deployment row carries ami_id in configuration (persisted by /deploy)
    persisted_ami = "ami-resume-y"
    deploys.get.return_value = {
        "deployment_id": deploy_id,
        "state": "TERMINATED",
        "configuration": {"model": {"artifact_uri": "hf://m"}, "ami_id": persisted_ami},
        "pool_id": pool_id,
        "target_pool_id": pool_id,
        "target_node_id": uuid4(),  # stale
        "gpu_per_replica": 1,
        "org_id": org_id,
        "engine": "vllm",
        "model_name": "m",
        "inference_model": None,
    }
    pool_repo = AsyncMock(spec=ComputePoolRepository)
    pool_repo.get.return_value = {
        "id": pool_id,
        "lifecycle_state": "running",
        "metadata": {},
        "allowed_gpu_types": ["g6.xlarge"],
        "region_constraint": ["us-east-1"],
        "provider_pool_id": "aws/g6.xlarge",
    }
    jobs_repo = AsyncMock(spec=ProvisioningJobRepository)
    controller = AsyncMock(spec=WorkerController)

    body, status = await deployment_server._start_deployment_impl(
        deployment_id=str(deploy_id),
        db_pool=db_pool,
        controller=controller,
        pool_repo=pool_repo,
        inventory=inventory,
        deploys=deploys,
        placer=placer,
        jobs_repo=jobs_repo,
    )

    assert status in (200, 202)
    assert body["state"] in ("PENDING_NODE", "DEPLOYING")

    # The persisted ami_id must have been forwarded to _build_provisioning_spec
    assert len(captured_ami_ids) >= 1, "_build_provisioning_spec was never called"
    assert captured_ami_ids[0] == persisted_ami, (
        f"Expected ami_id={persisted_ami!r} forwarded on resume, "
        f"got {captured_ami_ids[0]!r}"
    )


async def test_resume_ami_id_from_configuration_json_string(monkeypatch):
    """configuration stored as a JSON string (asyncpg jsonb->str) is decoded
    and ami_id extracted correctly on resume."""
    from orchestration.models.model_deployment.pool_placer import (
        PoolPlacer, ColdStart,
    )
    from orchestration.repositories.inventory_repo import (
        InventoryRepository,
    )
    from orchestration.repositories.model_deployment_repo import (
        ModelDeploymentRepository,
    )
    from orchestration.repositories.pool_repo import (
        ComputePoolRepository,
    )
    from orchestration.state_machine.jobs.repository import (
        ProvisioningJobRepository,
    )

    deploy_id = uuid4()
    pool_id = uuid4()
    node_id = uuid4()
    org_id = str(uuid4())

    captured_ami_ids: list[str | None] = []

    async def _spy_build_spec(*, pool_row, pool_meta, decision, org_id, ami_id=None):
        captured_ami_ids.append(ami_id)
        return {"provider": "aws", "instance_type": "g6.xlarge"}

    monkeypatch.setattr(
        deployment_server, "_build_provisioning_spec", _spy_build_spec
    )

    class _TxCtx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    class _AcquireCtx:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *exc):
            return False

    conn = MagicMock(name="conn")
    conn.transaction = MagicMock(return_value=_TxCtx())
    db_pool = MagicMock(name="db_pool")
    db_pool.acquire = MagicMock(return_value=_AcquireCtx(conn))

    placer = AsyncMock(spec=PoolPlacer)
    placer.place.return_value = ColdStart(gpu_total_per_node=1, provider="aws")
    inventory = AsyncMock(spec=InventoryRepository)
    inventory.create_placeholder.return_value = node_id
    deploys = AsyncMock(spec=ModelDeploymentRepository)

    # configuration arrives as a JSON string (asyncpg jsonb->str scenario)
    persisted_ami = "ami-str-encoded-z"
    deploys.get.return_value = {
        "deployment_id": deploy_id,
        "state": "FAILED",
        "configuration": json.dumps({
            "model": {"artifact_uri": "hf://m"},
            "ami_id": persisted_ami,
        }),
        "pool_id": pool_id,
        "target_pool_id": pool_id,
        "target_node_id": None,
        "gpu_per_replica": 1,
        "org_id": org_id,
        "engine": "vllm",
        "model_name": "m",
        "inference_model": None,
    }
    pool_repo = AsyncMock(spec=ComputePoolRepository)
    pool_repo.get.return_value = {
        "id": pool_id,
        "lifecycle_state": "running",
        "metadata": {},
        "allowed_gpu_types": ["g6.xlarge"],
        "region_constraint": ["us-east-1"],
        "provider_pool_id": "aws/g6.xlarge",
    }
    jobs_repo = AsyncMock(spec=ProvisioningJobRepository)
    controller = AsyncMock(spec=WorkerController)

    body, status = await deployment_server._start_deployment_impl(
        deployment_id=str(deploy_id),
        db_pool=db_pool,
        controller=controller,
        pool_repo=pool_repo,
        inventory=inventory,
        deploys=deploys,
        placer=placer,
        jobs_repo=jobs_repo,
    )

    assert status in (200, 202)
    assert len(captured_ami_ids) >= 1
    assert captured_ami_ids[0] == persisted_ami, (
        f"Expected ami_id={persisted_ami!r} from JSON-string configuration, "
        f"got {captured_ami_ids[0]!r}"
    )


async def test_resume_no_ami_id_in_configuration_passes_none(monkeypatch):
    """When configuration has no ami_id (older rows, non-vLLM engines),
    place_and_provision is called with ami_id=None so resolve_ami's auto-pick
    still applies — no regression."""
    from orchestration.models.model_deployment.pool_placer import (
        PoolPlacer, ColdStart,
    )
    from orchestration.repositories.inventory_repo import (
        InventoryRepository,
    )
    from orchestration.repositories.model_deployment_repo import (
        ModelDeploymentRepository,
    )
    from orchestration.repositories.pool_repo import (
        ComputePoolRepository,
    )
    from orchestration.state_machine.jobs.repository import (
        ProvisioningJobRepository,
    )

    deploy_id = uuid4()
    pool_id = uuid4()
    node_id = uuid4()
    org_id = str(uuid4())

    captured_ami_ids: list[str | None] = []

    async def _spy_build_spec(*, pool_row, pool_meta, decision, org_id, ami_id=None):
        captured_ami_ids.append(ami_id)
        return {"provider": "aws", "instance_type": "g6.xlarge"}

    monkeypatch.setattr(
        deployment_server, "_build_provisioning_spec", _spy_build_spec
    )

    class _TxCtx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    class _AcquireCtx:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *exc):
            return False

    conn = MagicMock(name="conn")
    conn.transaction = MagicMock(return_value=_TxCtx())
    db_pool = MagicMock(name="db_pool")
    db_pool.acquire = MagicMock(return_value=_AcquireCtx(conn))

    placer = AsyncMock(spec=PoolPlacer)
    placer.place.return_value = ColdStart(gpu_total_per_node=1, provider="aws")
    inventory = AsyncMock(spec=InventoryRepository)
    inventory.create_placeholder.return_value = node_id
    deploys = AsyncMock(spec=ModelDeploymentRepository)

    # configuration has no ami_id key (older row / ollama / non-vLLM)
    deploys.get.return_value = {
        "deployment_id": deploy_id,
        "state": "STOPPED",
        "configuration": {"model": {"artifact_uri": "hf://m"}},
        "pool_id": pool_id,
        "target_pool_id": pool_id,
        "target_node_id": None,
        "gpu_per_replica": 1,
        "org_id": org_id,
        "engine": "ollama",
        "model_name": "m",
        "inference_model": None,
    }
    pool_repo = AsyncMock(spec=ComputePoolRepository)
    pool_repo.get.return_value = {
        "id": pool_id,
        "lifecycle_state": "running",
        "metadata": {},
        "allowed_gpu_types": ["g6.xlarge"],
        "region_constraint": ["us-east-1"],
        "provider_pool_id": "aws/g6.xlarge",
    }
    jobs_repo = AsyncMock(spec=ProvisioningJobRepository)
    controller = AsyncMock(spec=WorkerController)

    body, status = await deployment_server._start_deployment_impl(
        deployment_id=str(deploy_id),
        db_pool=db_pool,
        controller=controller,
        pool_repo=pool_repo,
        inventory=inventory,
        deploys=deploys,
        placer=placer,
        jobs_repo=jobs_repo,
    )

    assert status in (200, 202)
    # ami_id=None must be passed — no regression for non-vLLM or older rows
    assert len(captured_ami_ids) >= 1
    assert captured_ami_ids[0] is None, (
        f"Expected ami_id=None for row without ami_id, got {captured_ami_ids[0]!r}"
    )
