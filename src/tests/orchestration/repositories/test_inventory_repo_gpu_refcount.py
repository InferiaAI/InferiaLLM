from __future__ import annotations
import asyncio
import os
import pytest
import pytest_asyncio
import asyncpg
from uuid import UUID, uuid4

from services.orchestration.repositories.inventory_repo import (
    InventoryRepository,
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


async def _seed_org_and_pool(p, *, gpu_count=4):
    org_id = uuid4()
    pool_id = uuid4()
    async with p.acquire() as c:
        await c.execute(
            "INSERT INTO organizations(id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            str(org_id), f"test-org-{org_id}",
        )
        await c.execute(
            """
            INSERT INTO compute_pools(
              id, pool_name, owner_type, owner_id, provider, pool_type,
              allowed_gpu_types, max_cost_per_hour, scheduling_policy,
              provider_pool_id, is_active, gpu_count, lifecycle_state
            )
            VALUES ($1, $2, 'organization', $3::text, 'aws', 'cluster',
                    ARRAY['t3.small']::text[], 10.0, '{}'::jsonb,
                    $4, true, $5, 'running')
            """,
            pool_id, f"p-{pool_id}", str(org_id),
            f"placeholder:{pool_id}", gpu_count,
        )
    return org_id, pool_id


async def _seed_node(p, pool_id, *, gpu_total=4, gpu_allocated=0,
                     state="ready", terminating=False):
    node_id = uuid4()
    async with p.acquire() as c:
        await c.execute(
            """
            INSERT INTO compute_inventory(
              id, pool_id, provider, provider_instance_id, hostname,
              node_name, agent_kind, gpu_total, gpu_allocated, vcpu_total,
              vcpu_allocated, ram_gb_total, ram_gb_allocated, state, metadata
            )
            VALUES ($1, $2, 'aws', $3, 'h', $4, 'worker',
                    $5, $6, 0, 0, 0, 0, $7,
                    CASE WHEN $8 THEN '{"terminating": true}'::jsonb ELSE '{}'::jsonb END)
            """,
            node_id, pool_id, str(node_id), f"node-{node_id}",
            gpu_total, gpu_allocated, state, terminating,
        )
    return node_id


async def test_allocate_gpu_succeeds_when_capacity_available(pool):
    repo = InventoryRepository(pool)
    _, pool_id = await _seed_org_and_pool(pool)
    node_id = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=0)
    ok = await repo.allocate_gpu(node_id, 2)
    assert ok is True
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", node_id
        )
    assert row["gpu_allocated"] == 2


async def test_allocate_gpu_fails_when_would_exceed_capacity(pool):
    repo = InventoryRepository(pool)
    _, pool_id = await _seed_org_and_pool(pool)
    node_id = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=3)
    ok = await repo.allocate_gpu(node_id, 2)
    assert ok is False
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", node_id
        )
    assert row["gpu_allocated"] == 3


async def test_allocate_gpu_succeeds_on_provisioning_node(pool):
    repo = InventoryRepository(pool)
    _, pool_id = await _seed_org_and_pool(pool)
    node_id = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=1,
                                state="provisioning")
    ok = await repo.allocate_gpu(node_id, 2)
    assert ok is True


async def test_allocate_gpu_fails_on_terminating_node(pool):
    repo = InventoryRepository(pool)
    _, pool_id = await _seed_org_and_pool(pool)
    node_id = await _seed_node(pool, pool_id, terminating=True)
    ok = await repo.allocate_gpu(node_id, 1)
    assert ok is False


async def test_release_gpu_decrements(pool):
    repo = InventoryRepository(pool)
    _, pool_id = await _seed_org_and_pool(pool)
    node_id = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=3)
    result = await repo.release_gpu(node_id, 1)
    assert result.new_allocated == 2
    assert result.should_destroy is False


async def test_release_gpu_to_zero_signals_destroy(pool):
    repo = InventoryRepository(pool)
    _, pool_id = await _seed_org_and_pool(pool)
    node_id = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=1)
    result = await repo.release_gpu(node_id, 1)
    assert result.new_allocated == 0
    assert result.should_destroy is True


async def test_release_gpu_to_zero_with_pending_deploy_does_not_destroy(pool):
    repo = InventoryRepository(pool)
    _, pool_id = await _seed_org_and_pool(pool)
    node_id = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=1)
    async with pool.acquire() as c:
        await c.execute(
            """
            INSERT INTO model_deployments(
              deployment_id, model_name, replicas, gpu_per_replica,
              pool_id, target_pool_id, target_node_id,
              state, org_id
            )
            VALUES ($1, 'm', 1, 1,
                    $2, $2, $3, 'PENDING_NODE', $4)
            """,
            uuid4(), pool_id, node_id, str(uuid4()),
        )
    result = await repo.release_gpu(node_id, 1)
    assert result.new_allocated == 0
    assert result.should_destroy is False


async def test_release_gpu_underflow_logs_and_no_destroy(pool, caplog):
    import logging
    caplog.set_level(logging.ERROR,
                      logger="services.orchestration.repositories.inventory_repo")
    repo = InventoryRepository(pool)
    _, pool_id = await _seed_org_and_pool(pool)
    node_id = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=0)
    result = await repo.release_gpu(node_id, 1)
    assert result.new_allocated == 0
    assert result.should_destroy is False
    # Use getMessage() so a formatter exception would surface here and
    # so the assertion actually exercises the interpolation path.
    assert any("refcount underflow" in r.getMessage().lower()
               for r in caplog.records)
    # Force-verify the node_id made it into the formatted output.
    assert any(str(node_id) in r.getMessage()
               for r in caplog.records)


async def test_create_placeholder_seeds_gpu_total_and_initial_alloc(pool):
    repo = InventoryRepository(pool)
    _, pool_id = await _seed_org_and_pool(pool)
    node_id = await repo.create_placeholder(
        pool_id=pool_id, gpu_total=4, initial_alloc=2,
    )
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT state, gpu_total, gpu_allocated, provider_instance_id "
            "FROM compute_inventory WHERE id=$1",
            node_id,
        )
    assert row["state"] == "provisioning"
    assert row["gpu_total"] == 4
    assert row["gpu_allocated"] == 2
    assert row["provider_instance_id"].startswith("placeholder:")


async def test_allocate_gpu_concurrent_no_double_book(pool):
    repo = InventoryRepository(pool)
    _, pool_id = await _seed_org_and_pool(pool)
    node_id = await _seed_node(pool, pool_id, gpu_total=1, gpu_allocated=0)
    results = await asyncio.gather(
        repo.allocate_gpu(node_id, 1),
        repo.allocate_gpu(node_id, 1),
    )
    assert sorted(results) == [False, True]
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", node_id
        )
    assert row["gpu_allocated"] == 1


async def test_allocate_gpu_uses_external_tx(pool):
    """allocate_gpu under a caller's tx commits when the caller commits."""
    repo = InventoryRepository(pool)
    _, pool_id = await _seed_org_and_pool(pool)
    node_id = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=0)

    async with pool.acquire() as conn:
        async with conn.transaction():
            ok = await repo.allocate_gpu(node_id, 2, tx=conn)
            assert ok is True
            # Inside the same tx, the update is visible.
            row = await conn.fetchrow(
                "SELECT gpu_allocated FROM compute_inventory WHERE id=$1",
                node_id,
            )
            assert row["gpu_allocated"] == 2
    # After commit, the update persists outside the tx.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1",
            node_id,
        )
    assert row["gpu_allocated"] == 2


async def test_create_placeholder_uses_external_tx(pool):
    repo = InventoryRepository(pool)
    _, pool_id = await _seed_org_and_pool(pool)

    async with pool.acquire() as conn:
        async with conn.transaction():
            node_id = await repo.create_placeholder(
                pool_id=pool_id, gpu_total=4, initial_alloc=2, tx=conn,
            )
            row = await conn.fetchrow(
                "SELECT state, gpu_total FROM compute_inventory WHERE id=$1",
                node_id,
            )
            assert row["state"] == "provisioning"
            assert row["gpu_total"] == 4
