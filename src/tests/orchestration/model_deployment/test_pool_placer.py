from __future__ import annotations
import os
import pytest
import pytest_asyncio
import asyncpg
from uuid import uuid4

from orchestration.models.model_deployment.pool_placer import (
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
    """Two place() calls under separate transactions where the first's
    tx is still open when the second runs — the second's FOR UPDATE
    SKIP LOCKED deterministically skips the row the first locked.

    This exercises the production contract that T7's deploy_model
    relies on: a long-lived placement transaction holds rows across
    subsequent placement attempts in other transactions.
    """
    placer = PoolPlacer(pool)
    pool_id = await _seed_pool_row(pool)
    n1 = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=2)  # free=2
    n2 = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=3)  # free=1

    async with pool.acquire() as conn_a:
        async with conn_a.transaction():
            d_a = await placer.place(
                pool_id=pool_id, gpu_required=1, tx=conn_a,
            )
            assert isinstance(d_a, BindToReady)

            # conn_a's transaction is still open — its row lock on d_a.node_id
            # is held. A second placer call on a separate transaction must
            # see that row as locked and skip to the other node.
            async with pool.acquire() as conn_b:
                async with conn_b.transaction():
                    d_b = await placer.place(
                        pool_id=pool_id, gpu_required=1, tx=conn_b,
                    )
                    assert isinstance(d_b, BindToReady)

            # Both nodes get picked exactly once — no double-binding.
            assert d_a.node_id != d_b.node_id
            assert {d_a.node_id, d_b.node_id} == {n1, n2}


async def test_place_uses_external_tx_when_provided(pool):
    """When called inside an explicit transaction, the row lock holds
    until that transaction commits. A concurrent place() running in
    its own short transaction sees the locked row skipped and falls
    through to ColdStart (or another node).
    """
    placer = PoolPlacer(pool)
    pool_id = await _seed_pool_row(pool)
    n1 = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=0)

    async with pool.acquire() as conn_a:
        async with conn_a.transaction():
            d_a = await placer.place(
                pool_id=pool_id, gpu_required=1, tx=conn_a,
            )
            assert isinstance(d_a, BindToReady)
            assert d_a.node_id == n1

            # While conn_a holds the row lock, a separate placer() call
            # should not see n1 — it's locked. There are no other nodes,
            # so it falls through to ColdStart.
            d_b = await placer.place(pool_id=pool_id, gpu_required=1)
            assert isinstance(d_b, ColdStart)
