"""Real-PG concurrency tests for ProvisioningJobRepository.

Verifies that claim_next_job's FOR UPDATE SKIP LOCKED actually prevents
double-claim under contention. Skipped unless INFERIA_TEST_DATABASE_URL
is set. Requires the 20260528a + 20260528b migrations to be applied
to the target DB."""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
import pytest

from inferia.services.orchestration.services.provisioning.jobs.repository import (
    ProvisioningJobRepository,
)


MIGRATIONS = [
    Path(__file__).resolve().parents[6] / "infra" / "schema" / "migrations" / "20260528a_node_state_failed.sql",
    Path(__file__).resolve().parents[6] / "infra" / "schema" / "migrations" / "20260528b_provisioning_jobs.sql",
]


@pytest.fixture
def test_database_url() -> str:
    url = os.environ.get("INFERIA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("INFERIA_TEST_DATABASE_URL not set")
    return url


async def _apply_migrations(pool):
    async with pool.acquire() as conn:
        for path in MIGRATIONS:
            sql = path.read_text()
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                await conn.execute(stmt)


@pytest.fixture
async def pool(test_database_url):
    pool = await asyncpg.create_pool(test_database_url, min_size=2, max_size=20)
    await _apply_migrations(pool)
    yield pool
    await pool.close()


class PoolDB:
    def __init__(self, pool):
        self.pool = pool
    def acquire(self):
        return self.pool.acquire()


async def _seed_job(pool, *, n: int = 20):
    """Insert n pending jobs and a fake compute_inventory parent row each.
    Returns the list of inserted job ids."""
    async with pool.acquire() as conn:
        pool_id = uuid.uuid4()
        await conn.execute(
            """INSERT INTO compute_pools (
                 id, pool_name, owner_type, provider, scheduling_policy,
                 lifecycle_state, org_id
               )
               VALUES (
                 $1, 'p-concurrency', 'organization', 'aws', '{}'::jsonb,
                 'running', 'org-concurrency'
               )
               ON CONFLICT (id) DO NOTHING""",
            pool_id,
        )
        ids = []
        for _ in range(n):
            node_id = uuid.uuid4()
            await conn.execute(
                """INSERT INTO compute_inventory (id, pool_id, provider,
                       provider_instance_id, state, agent_kind)
                   VALUES ($1, $2, 'aws', 'placeholder:c-'||$1::text, 'provisioning', 'worker')""",
                node_id, pool_id,
            )
            job_id = uuid.uuid4()
            await conn.execute(
                """INSERT INTO provisioning_jobs (
                       id, node_id, pool_id, org_id, provider, spec, phase
                   ) VALUES ($1, $2, $3, 'org-concurrency', 'aws', '{}'::jsonb, 'pending')""",
                job_id, node_id, pool_id,
            )
            ids.append(job_id)
    return ids, pool_id


@pytest.mark.asyncio
async def test_no_double_claim_under_20_concurrent_claimers(pool):
    """20 workers all calling claim_next_job → each gets a unique job."""
    job_ids, pool_id = await _seed_job(pool, n=20)
    repo = ProvisioningJobRepository(PoolDB(pool))

    async def claim(holder: str):
        return await repo.claim_next_job(lease_holder=holder, lease_seconds=300)

    results = await asyncio.gather(
        *[claim(f"worker-{i}") for i in range(20)]
    )
    claimed_ids = [job.id for job in results if job is not None]
    assert len(claimed_ids) == 20
    assert len(set(claimed_ids)) == 20  # all unique — no double-claim

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM provisioning_jobs WHERE id = ANY($1)", job_ids)
        await conn.execute("DELETE FROM compute_inventory WHERE pool_id = $1", pool_id)
        await conn.execute("DELETE FROM compute_pools WHERE id = $1", pool_id)


@pytest.mark.asyncio
async def test_claim_skips_leased_jobs(pool):
    """A job already leased by holder A is not claimable by holder B until
    the lease expires."""
    job_ids, pool_id = await _seed_job(pool, n=1)
    repo = ProvisioningJobRepository(PoolDB(pool))

    first = await repo.claim_next_job(lease_holder="A", lease_seconds=300)
    assert first is not None
    second = await repo.claim_next_job(lease_holder="B", lease_seconds=300)
    assert second is None

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM provisioning_jobs WHERE id = ANY($1)", job_ids)
        await conn.execute("DELETE FROM compute_inventory WHERE pool_id = $1", pool_id)
        await conn.execute("DELETE FROM compute_pools WHERE id = $1", pool_id)


@pytest.mark.asyncio
async def test_claim_picks_up_expired_lease(pool):
    """If a lease's lease_expires_at < now(), another reconciler claims it."""
    job_ids, pool_id = await _seed_job(pool, n=1)
    repo = ProvisioningJobRepository(PoolDB(pool))

    # Holder A grabs it but with a 0-second lease (already expired).
    first = await repo.claim_next_job(lease_holder="A", lease_seconds=0)
    assert first is not None

    # Holder B claims because A's lease is already expired.
    second = await repo.claim_next_job(lease_holder="B", lease_seconds=300)
    assert second is not None
    assert second.id == first.id
    assert second.lease_holder == "B"

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM provisioning_jobs WHERE id = ANY($1)", job_ids)
        await conn.execute("DELETE FROM compute_inventory WHERE pool_id = $1", pool_id)
        await conn.execute("DELETE FROM compute_pools WHERE id = $1", pool_id)


@pytest.mark.asyncio
async def test_cancelling_jobs_have_priority(pool):
    """Jobs in 'cancelling' phase are claimed before plain 'pending' jobs."""
    job_ids, pool_id = await _seed_job(pool, n=2)
    repo = ProvisioningJobRepository(PoolDB(pool))

    # Mark one job as 'cancelling' (the second).
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE provisioning_jobs SET phase='cancelling' WHERE id=$1",
            job_ids[1],
        )

    claimed = await repo.claim_next_job(lease_holder="W", lease_seconds=300)
    assert claimed is not None
    assert claimed.id == job_ids[1]

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM provisioning_jobs WHERE id = ANY($1)", job_ids)
        await conn.execute("DELETE FROM compute_inventory WHERE pool_id = $1", pool_id)
        await conn.execute("DELETE FROM compute_pools WHERE id = $1", pool_id)
