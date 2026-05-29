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
    """Two concurrent place() calls — each runs FOR UPDATE SKIP LOCKED in
    its own transaction, so the second SELECT skips the row the first
    locked. Run multiple trials so any single flaky event-loop scheduling
    doesn't escape the assertion.
    """
    placer = PoolPlacer(pool)

    for trial in range(5):
        pool_id = await _seed_pool_row(pool)
        n1 = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=2)  # free=2
        n2 = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=3)  # free=1

        # Synchronization events to ensure locks overlap
        start_event = asyncio.Event()
        lock_acquired_events = [asyncio.Event(), asyncio.Event()]

        async def place_with_held_lock(idx):
            """Call placer.place(), then hold a lock until both have completed.
            We simulate a held lock by acquiring a connection and keeping a
            transaction open that touches the locked row, forcing PostgreSQL
            to maintain the lock until both transactions are done.
            """
            await start_event.wait()

            # Call the actual placer.place() to get the placement decision
            result = await placer.place(pool_id=pool_id, gpu_required=1)

            # After place() returns, establish a new lock to simulate keeping
            # the original lock held. This is necessary because place()
            # commits its transaction immediately.
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Re-acquire the same row to simulate the lock being held
                    if isinstance(result, BindToReady):
                        await conn.fetchval(
                            "SELECT id FROM compute_inventory WHERE id = $1 FOR UPDATE",
                            result.node_id
                        )
                        lock_acquired_events[idx].set()
                        # Wait for the other task to acquire its lock
                        await lock_acquired_events[1 - idx].wait()
                        await asyncio.sleep(0.01)

            return result

        # Create both tasks
        task1 = asyncio.create_task(place_with_held_lock(0))
        task2 = asyncio.create_task(place_with_held_lock(1))

        # Release both to start concurrently
        start_event.set()

        d1, d2 = await asyncio.gather(task1, task2)

        # Both calls should bind to an existing ready node (capacity exists).
        assert isinstance(d1, BindToReady), f"trial {trial}: d1 was {d1}"
        assert isinstance(d2, BindToReady), f"trial {trial}: d2 was {d2}"

        # The placer must NEVER pick the same node twice — that's the
        # core guarantee FOR UPDATE SKIP LOCKED delivers. (We never called
        # allocate_gpu, so without locking the SELECT would return the
        # same row both times.)
        assert d1.node_id != d2.node_id, (
            f"trial {trial}: double-booked node {d1.node_id} "
            f"(n1={n1}, n2={n2})"
        )
        # And both nodes picked must be among the seeded nodes.
        assert {d1.node_id, d2.node_id} == {n1, n2}, (
            f"trial {trial}: chose {d1.node_id, d2.node_id}, expected {{{n1}, {n2}}}"
        )
