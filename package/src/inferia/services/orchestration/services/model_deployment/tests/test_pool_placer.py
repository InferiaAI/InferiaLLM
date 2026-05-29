from __future__ import annotations
import asyncio
import os
import pytest
import pytest_asyncio
import asyncpg
from uuid import UUID, uuid4

from inferia.services.orchestration.services.model_deployment.pool_placer import (
    PoolPlacer,
    BindToReady,
    CoWaitOnProvisioning,
    ColdStart,
    PoolAtCapacity,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def pool():
    dsn = os.getenv("TEST_DATABASE_URL",
                    "postgresql://inferia:inferia@localhost:5432/inferia_test")
    p = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
    yield p
    await p.close()


async def _seed_pool_row(p, *, gpu_count=4, max_nodes=None, provider="aws"):
    org_id, pool_id = uuid4(), uuid4()
    async with p.acquire() as c:
        await c.execute("INSERT INTO organizations(id,name) VALUES($1,$2) "
                         "ON CONFLICT DO NOTHING",
                         str(org_id), f"o-{org_id}")
        await c.execute(
            """INSERT INTO compute_pools(id, pool_name, owner_type, owner_id,
                 provider, pool_type, allowed_gpu_types, max_cost_per_hour,
                 scheduling_policy, provider_pool_id, is_active, lifecycle_state,
                 gpu_count, max_nodes)
               VALUES ($1, $2, 'organization', $3::text, $4, 'cluster',
                       ARRAY['t3.small'], 10.0, '{}', $5, true, 'running',
                       $6, $7)""",
            pool_id, f"p-{pool_id}", str(org_id), provider,
            f"placeholder:{pool_id}", gpu_count, max_nodes,
        )
    return pool_id


async def _seed_node(p, pool_id, *, gpu_total=4, gpu_allocated=0,
                     state="ready", terminating=False):
    nid = uuid4()
    async with p.acquire() as c:
        await c.execute(
            """INSERT INTO compute_inventory(id, pool_id, provider,
                 provider_instance_id, hostname, node_name, agent_kind,
                 gpu_total, gpu_allocated, vcpu_total, vcpu_allocated,
                 ram_gb_total, ram_gb_allocated, state, metadata)
               VALUES ($1, $2, 'aws', $3, 'h', $4, 'worker',
                       $5, $6, 0, 0, 0, 0, $7,
                       CASE WHEN $8 THEN '{"terminating": true}'::jsonb ELSE '{}'::jsonb END)""",
            nid, pool_id, f"p-{nid}", f"n-{nid}",
            gpu_total, gpu_allocated, state, terminating,
        )
    return nid


async def test_empty_pool_returns_coldstart(pool):
    placer = PoolPlacer(pool)
    pool_id = await _seed_pool_row(pool)
    decision = await placer.place(pool_id=pool_id, gpu_required=1)
    assert isinstance(decision, ColdStart)
    assert decision.gpu_total_per_node == 4
    assert decision.provider == "aws"


async def test_ready_with_capacity_returns_bind_to_ready(pool):
    placer = PoolPlacer(pool)
    pool_id = await _seed_pool_row(pool)
    node_id = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=1)
    decision = await placer.place(pool_id=pool_id, gpu_required=1)
    assert isinstance(decision, BindToReady)
    assert decision.node_id == node_id


async def test_full_ready_returns_coldstart(pool):
    placer = PoolPlacer(pool)
    pool_id = await _seed_pool_row(pool)
    await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=4)
    decision = await placer.place(pool_id=pool_id, gpu_required=1)
    assert isinstance(decision, ColdStart)


async def test_best_fit_picks_smallest_free(pool):
    placer = PoolPlacer(pool)
    pool_id = await _seed_pool_row(pool)
    smaller = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=2)
    await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=0)  # free=4
    decision = await placer.place(pool_id=pool_id, gpu_required=1)
    assert isinstance(decision, BindToReady)
    assert decision.node_id == smaller


async def test_provisioning_placeholder_returns_cowait(pool):
    placer = PoolPlacer(pool)
    pool_id = await _seed_pool_row(pool)
    nid = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=1,
                            state="provisioning")
    decision = await placer.place(pool_id=pool_id, gpu_required=2)
    assert isinstance(decision, CoWaitOnProvisioning)
    assert decision.node_id == nid


async def test_ready_preferred_over_provisioning(pool):
    placer = PoolPlacer(pool)
    pool_id = await _seed_pool_row(pool)
    ready = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=3,
                              state="ready")
    await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=0,
                      state="provisioning")
    decision = await placer.place(pool_id=pool_id, gpu_required=1)
    assert isinstance(decision, BindToReady)
    assert decision.node_id == ready


async def test_terminating_node_excluded(pool):
    placer = PoolPlacer(pool)
    pool_id = await _seed_pool_row(pool)
    await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=0,
                      terminating=True)
    decision = await placer.place(pool_id=pool_id, gpu_required=1)
    assert isinstance(decision, ColdStart)


async def test_max_nodes_cap_raises_pool_at_capacity(pool):
    placer = PoolPlacer(pool)
    pool_id = await _seed_pool_row(pool, max_nodes=1)
    await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=4)
    with pytest.raises(PoolAtCapacity) as exc:
        await placer.place(pool_id=pool_id, gpu_required=1)
    assert exc.value.current_nodes == 1
    assert exc.value.max_nodes == 1


async def test_max_nodes_null_means_unlimited(pool):
    placer = PoolPlacer(pool)
    pool_id = await _seed_pool_row(pool, max_nodes=None)
    for _ in range(5):
        await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=4)
    decision = await placer.place(pool_id=pool_id, gpu_required=1)
    assert isinstance(decision, ColdStart)


async def test_concurrent_place_picks_distinct_nodes_under_capacity(pool):
    """Two concurrent place() calls — each row is FOR UPDATE SKIP LOCKED
    inside its own transaction, so the second call's SELECT skips the
    row the first call holds and picks the next-best candidate.
    """
    placer = PoolPlacer(pool)
    pool_id = await _seed_pool_row(pool)
    n1 = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=2)  # free=2
    n2 = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=3)  # free=1

    # Synchronization: ensure both transactions start concurrently
    start_event = asyncio.Event()
    lock_acquired_events = [asyncio.Event(), asyncio.Event()]

    async def place_with_hold(idx):
        """Wrapper that holds the lock to ensure transaction overlap."""
        await start_event.wait()

        # We need to hold the connection open to maintain the lock
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT id, state
                      FROM compute_inventory
                     WHERE pool_id = $1
                       AND state IN ('ready', 'provisioning')
                       AND (metadata->>'terminating') IS DISTINCT FROM 'true'
                       AND gpu_total - gpu_allocated >= $2
                  ORDER BY (CASE WHEN state = 'ready' THEN 0 ELSE 1 END),
                           (gpu_total - gpu_allocated) ASC
                     LIMIT 1
                       FOR UPDATE SKIP LOCKED
                    """,
                    pool_id, 1,
                )
                lock_acquired_events[idx].set()
                # Hold the lock while both transactions' locks are acquired
                if idx == 0:
                    # First transaction holds the lock longer
                    await lock_acquired_events[1].wait()
                    await asyncio.sleep(0.01)
                else:
                    # Second transaction just waits a bit
                    await asyncio.sleep(0.01)
                return row["id"] if row else None

    # Start both operations
    task1 = asyncio.create_task(place_with_hold(0))
    task2 = asyncio.create_task(place_with_hold(1))
    start_event.set()

    r1, r2 = await asyncio.gather(task1, task2)

    chosen = {r1, r2}
    chosen.discard(None)
    assert chosen == {n1, n2}
