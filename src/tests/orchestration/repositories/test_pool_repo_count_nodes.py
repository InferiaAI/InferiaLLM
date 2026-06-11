from __future__ import annotations
import os
import pytest
import pytest_asyncio
import asyncpg
from uuid import uuid4

from orchestration.repositories.pool_repo import (
    ComputePoolRepository,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def pool():
    dsn = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql://inferia:inferia@localhost:5432/inferia_test",
    )
    p = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    yield p
    await p.close()


async def _seed_pool(p):
    org_id, pool_id = uuid4(), uuid4()
    async with p.acquire() as c:
        await c.execute(
            "INSERT INTO organizations(id, name) VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING",
            str(org_id), f"o-{org_id}",
        )
        await c.execute(
            """INSERT INTO compute_pools(id, pool_name, owner_type, owner_id,
                 provider, pool_type, allowed_gpu_types, max_cost_per_hour,
                 scheduling_policy, provider_pool_id, is_active, lifecycle_state,
                 gpu_count)
               VALUES ($1, $2, 'organization', $3::text, 'aws', 'cluster',
                       ARRAY['t3.small'], 10.0, '{}', $4, true, 'running', 1)""",
            str(pool_id), f"p-{pool_id}", str(org_id),
            f"placeholder:{pool_id}",
        )
    return pool_id


async def _seed_node(p, pool_id, *, state="ready", terminating=False):
    nid = uuid4()
    async with p.acquire() as c:
        await c.execute(
            """INSERT INTO compute_inventory(id, pool_id, provider,
                 provider_instance_id, hostname, node_name, agent_kind,
                 gpu_total, gpu_allocated, vcpu_total, vcpu_allocated,
                 ram_gb_total, ram_gb_allocated, state, metadata)
               VALUES ($1, $2, 'aws', $3, 'h', $4, 'worker',
                       1, 0, 0, 0, 0, 0, $5,
                       CASE WHEN $6 THEN '{"terminating": true}'::jsonb ELSE '{}'::jsonb END)""",
            str(nid), str(pool_id), f"p-{nid}", f"n-{nid}", state, terminating,
        )
    return nid


async def test_count_nodes_excludes_terminated_and_terminating(pool):
    repo = ComputePoolRepository(pool)
    pool_id = await _seed_pool(pool)
    await _seed_node(pool, pool_id, state="ready")
    await _seed_node(pool, pool_id, state="provisioning")
    await _seed_node(pool, pool_id, state="terminated")
    await _seed_node(pool, pool_id, state="ready", terminating=True)

    n = await repo.count_nodes(pool_id)
    assert n == 2  # ready + provisioning; not terminated; not metadata.terminating


async def test_count_nodes_empty_pool_returns_zero(pool):
    repo = ComputePoolRepository(pool)
    pool_id = await _seed_pool(pool)
    assert await repo.count_nodes(pool_id) == 0


async def test_count_nodes_unknown_pool_returns_zero(pool):
    repo = ComputePoolRepository(pool)
    assert await repo.count_nodes(uuid4()) == 0
