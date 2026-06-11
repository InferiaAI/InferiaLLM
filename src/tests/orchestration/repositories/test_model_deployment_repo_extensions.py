from __future__ import annotations
import os
from datetime import datetime, timezone
import pytest
import pytest_asyncio
import asyncpg
from uuid import UUID, uuid4

from services.orchestration.repositories.model_deployment_repo import (
    ModelDeploymentRepository,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def pool():
    dsn = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql://inferia:inferia@localhost:5432/inferia_test",
    )
    p = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
    yield p
    await p.close()


async def _seed_pool_row(pool):
    org_id = uuid4()
    pool_id = uuid4()
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO organizations(id, name) VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING",
            str(org_id), f"o-{org_id}",
        )
        await c.execute(
            """
            INSERT INTO compute_pools(id, pool_name, owner_type, owner_id,
              provider, pool_type, allowed_gpu_types, max_cost_per_hour,
              scheduling_policy, provider_pool_id, is_active, lifecycle_state,
              gpu_count)
            VALUES ($1, $2, 'organization', $3::text, 'aws', 'cluster',
                    ARRAY['t3.small'], 10.0, '{}', $4, true, 'running', 1)
            """,
            str(pool_id), f"p-{pool_id}", str(org_id), f"placeholder:{pool_id}",
        )
    return org_id, pool_id


async def _seed_deploy(pool, *, pool_id, state, target_node_id=None,
                       created_at=None):
    deploy_id = uuid4()
    async with pool.acquire() as c:
        await c.execute(
            """
            INSERT INTO model_deployments(deployment_id, model_name,
              replicas, gpu_per_replica, pool_id,
              target_pool_id, target_node_id, state, org_id, created_at)
            VALUES ($1, 'm', 1, 1,
                    $2, $2, $3, $4, $5,
                    COALESCE($6, NOW()))
            """,
            str(deploy_id), str(pool_id), str(target_node_id) if target_node_id else None, state, str(uuid4()),
            created_at,
        )
    return deploy_id


async def test_list_pending_for_pool_returns_fifo(pool):
    repo = ModelDeploymentRepository(pool, event_bus=None)
    _, pool_id = await _seed_pool_row(pool)

    early = await _seed_deploy(pool, pool_id=pool_id, state="PENDING_NODE",
                                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    late = await _seed_deploy(pool, pool_id=pool_id, state="PENDING_NODE",
                                created_at=datetime(2026, 5, 1, tzinfo=timezone.utc))
    await _seed_deploy(pool, pool_id=pool_id, state="RUNNING")  # ignored

    rows = await repo.list_pending_for_pool(pool_id)
    ids = [r["id"] for r in rows]
    assert ids == [early, late]


async def test_list_pending_for_pool_includes_inference_model(pool):
    """Regression: the cold-path linker resolves the model from inference_model;
    if the SELECT drops it, resolve_artifact_uri falls back to model_name and
    the worker pulls the wrong model (e.g. 'gemma3-ollama' instead of
    'gemma3:4b' -> ollama 'file does not exist')."""
    repo = ModelDeploymentRepository(pool, event_bus=None)
    _, pool_id = await _seed_pool_row(pool)
    did = uuid4()
    async with pool.acquire() as c:
        await c.execute(
            """
            INSERT INTO model_deployments(deployment_id, model_name,
              inference_model, replicas, gpu_per_replica, pool_id,
              target_pool_id, state, org_id)
            VALUES ($1, 'gemma3-ollama', 'gemma3:4b', 1, 1, $2, $2,
                    'PENDING_NODE', $3)
            """,
            str(did), str(pool_id), str(uuid4()),
        )
    rows = await repo.list_pending_for_pool(pool_id)
    row = next(r for r in rows if r["id"] == did)
    assert row["inference_model"] == "gemma3:4b"
    assert row["model_name"] == "gemma3-ollama"


async def test_bind_to_node_sets_target_node_id(pool):
    repo = ModelDeploymentRepository(pool, event_bus=None)
    _, pool_id = await _seed_pool_row(pool)
    deploy_id = await _seed_deploy(pool, pool_id=pool_id, state="CREATED")
    node_id = uuid4()
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO compute_inventory(id, pool_id, provider, "
            "provider_instance_id, hostname, node_name, agent_kind, "
            "gpu_total, gpu_allocated, vcpu_total, vcpu_allocated, "
            "ram_gb_total, ram_gb_allocated, state) "
            "VALUES ($1, $2, 'aws', $3, 'h', 'n', 'worker', 1, 0, 0, 0, 0, 0, 'ready')",
            str(node_id), str(pool_id), f"p-{node_id}",
        )

    await repo.bind_to_node(deploy_id, node_id)

    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT target_node_id FROM model_deployments "
            "WHERE deployment_id=$1",
            deploy_id,
        )
    assert row["target_node_id"] == node_id


async def test_set_state_changes_state(pool):
    repo = ModelDeploymentRepository(pool, event_bus=None)
    _, pool_id = await _seed_pool_row(pool)
    deploy_id = await _seed_deploy(pool, pool_id=pool_id, state="CREATED")

    await repo.set_state(deploy_id, "PENDING_NODE")

    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT state FROM model_deployments WHERE deployment_id=$1",
            deploy_id,
        )
    assert row["state"] == "PENDING_NODE"


async def test_unbind_clears_target_node_id(pool):
    repo = ModelDeploymentRepository(pool, event_bus=None)
    _, pool_id = await _seed_pool_row(pool)
    node_id = uuid4()
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO compute_inventory(id, pool_id, provider, "
            "provider_instance_id, hostname, node_name, agent_kind, "
            "gpu_total, gpu_allocated, vcpu_total, vcpu_allocated, "
            "ram_gb_total, ram_gb_allocated, state) "
            "VALUES ($1, $2, 'aws', $3, 'h', 'n', 'worker', 1, 0, 0, 0, 0, 0, 'ready')",
            str(node_id), str(pool_id), f"p-{node_id}",
        )
    deploy_id = await _seed_deploy(pool, pool_id=pool_id, state="PENDING_NODE",
                                    target_node_id=node_id)

    await repo.unbind(deploy_id)

    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT target_node_id FROM model_deployments "
            "WHERE deployment_id=$1",
            deploy_id,
        )
    assert row["target_node_id"] is None


async def test_list_pending_for_pool_locks_under_explicit_tx(pool):
    """When called inside an explicit transaction, FOR UPDATE SKIP LOCKED
    actually locks the matched rows — a concurrent transaction with the
    same query sees zero rows (SKIPped).

    Proves the C1 fix: the locking guarantee promised by the docstring
    is delivered only when the caller passes its own `tx`.
    """
    repo = ModelDeploymentRepository(pool, event_bus=None)
    _, pool_id = await _seed_pool_row(pool)
    await _seed_deploy(pool, pool_id=pool_id, state="PENDING_NODE")
    await _seed_deploy(pool, pool_id=pool_id, state="PENDING_NODE")

    async with pool.acquire() as conn_a:
        async with conn_a.transaction():
            locked_a = await repo.list_pending_for_pool(pool_id, tx=conn_a)
            assert len(locked_a) == 2

            # While the above transaction holds the locks, a concurrent
            # caller with FOR UPDATE SKIP LOCKED should see zero matched
            # rows because all matches are locked.
            async with pool.acquire() as conn_b:
                async with conn_b.transaction():
                    locked_b = await repo.list_pending_for_pool(
                        pool_id, tx=conn_b,
                    )
                    assert locked_b == []


async def test_list_pending_for_pool_without_tx_releases_locks(pool):
    """When called without an explicit tx, the FOR UPDATE locks are
    released as soon as the SELECT returns — concurrent callers see
    the same rows.
    """
    repo = ModelDeploymentRepository(pool, event_bus=None)
    _, pool_id = await _seed_pool_row(pool)
    await _seed_deploy(pool, pool_id=pool_id, state="PENDING_NODE")

    first = await repo.list_pending_for_pool(pool_id)
    second = await repo.list_pending_for_pool(pool_id)
    assert len(first) == len(second) == 1  # both see the row
