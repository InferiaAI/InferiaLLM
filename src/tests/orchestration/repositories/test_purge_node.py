"""Real-PG tests for InventoryRepository.purge_node().

purge_node() is the authoritative hard-delete for a node and ALL of its DB
residue: it fails+unbinds any deployment still pointing at the node, then
deletes the node's node_provisioning_events, worker_bootstrap_tokens,
provisioning_jobs, and finally the compute_inventory row itself.

These tests run against the throwaway test DB wired into the inferia-test
container (TEST_DATABASE_URL / INFERIA_TEST_DATABASE_URL ->
postgresql://inferia:inferia@inferia-testpg:5432/inferia_test, full schema
already loaded). Each test seeds its own org/pool/node and tears everything
down again so the shared DB stays clean.
"""
from __future__ import annotations
import os
import pytest
import pytest_asyncio
import asyncpg
from uuid import uuid4

from services.orchestration.repositories.inventory_repo import (
    InventoryRepository,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def pool():
    dsn = os.getenv(
        "TEST_DATABASE_URL",
        os.getenv(
            "INFERIA_TEST_DATABASE_URL",
            "postgresql://inferia:inferia@inferia-testpg:5432/inferia_test",
        ),
    )
    p = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
    yield p
    await p.close()


async def _seed_org_and_pool(p):
    org_id = uuid4()
    pool_id = uuid4()
    async with p.acquire() as c:
        await c.execute(
            "INSERT INTO organizations(id, name) VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING",
            str(org_id), f"test-org-{org_id}",
        )
        await c.execute(
            """
            INSERT INTO compute_pools(
              id, pool_name, owner_type, owner_id, org_id, provider, pool_type,
              allowed_gpu_types, max_cost_per_hour, scheduling_policy,
              provider_pool_id, is_active, gpu_count, lifecycle_state
            )
            VALUES ($1, $2, 'organization', $3::text, $3::text, 'aws',
                    'cluster', ARRAY['t3.small']::text[], 10.0, '{}'::jsonb,
                    $4, true, 4, 'running')
            """,
            pool_id, f"p-{pool_id}", str(org_id), f"placeholder:{pool_id}",
        )
    return org_id, pool_id


async def _seed_node(p, pool_id, *, state="ready"):
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
                    4, 0, 0, 0, 0, 0, $5, '{}'::jsonb)
            """,
            node_id, pool_id, str(node_id), f"node-{node_id}", state,
        )
    return node_id


async def _seed_provisioning_job(p, pool_id, node_id, org_id):
    job_id = uuid4()
    async with p.acquire() as c:
        await c.execute(
            """
            INSERT INTO provisioning_jobs(
              id, node_id, pool_id, org_id, provider, spec, phase
            )
            VALUES ($1, $2, $3, $4, 'aws', '{}'::jsonb, 'pending')
            """,
            job_id, node_id, pool_id, str(org_id),
        )
    return job_id


async def _seed_event(p, pool_id, node_id):
    async with p.acquire() as c:
        return await c.fetchval(
            """
            INSERT INTO node_provisioning_events(
              pool_id, node_id, phase, status, message
            )
            VALUES ($1, $2, 'preflight', 'running', 'seeding')
            RETURNING id
            """,
            pool_id, node_id,
        )


async def _seed_bootstrap_token(p, pool_id, org_id, node_id):
    token_id = uuid4()
    async with p.acquire() as c:
        await c.execute(
            """
            INSERT INTO worker_bootstrap_tokens(
              id, token_hash, pool_id, org_id, expires_at, consumed_node_id
            )
            VALUES ($1, $2, $3, $4, now() + interval '1 hour', $5)
            """,
            token_id, f"hash-{token_id}", pool_id, str(org_id), node_id,
        )
    return token_id


async def _seed_deployment(p, pool_id, node_id, org_id, *, state="RUNNING"):
    deployment_id = uuid4()
    async with p.acquire() as c:
        await c.execute(
            """
            INSERT INTO model_deployments(
              deployment_id, model_name, replicas, gpu_per_replica,
              pool_id, target_pool_id, target_node_id, endpoint,
              state, org_id
            )
            VALUES ($1, 'm', 1, 1, $2, $2, $3, 'http://node:8080',
                    $4, $5)
            """,
            deployment_id, pool_id, node_id, state, str(org_id),
        )
    return deployment_id


async def _cleanup(p, *, org_id=None, pool_id=None, node_id=None,
                   deployment_ids=()):
    """Best-effort teardown so the shared test DB stays clean."""
    async with p.acquire() as c:
        for dep_id in deployment_ids:
            await c.execute(
                "DELETE FROM model_deployments WHERE deployment_id=$1", dep_id
            )
        if node_id is not None:
            await c.execute(
                "DELETE FROM node_provisioning_events WHERE node_id=$1", node_id
            )
            await c.execute(
                "DELETE FROM worker_bootstrap_tokens WHERE consumed_node_id=$1",
                node_id,
            )
            await c.execute(
                "DELETE FROM provisioning_jobs WHERE node_id=$1", node_id
            )
            await c.execute(
                "DELETE FROM compute_inventory WHERE id=$1", node_id
            )
        if pool_id is not None:
            await c.execute(
                "DELETE FROM worker_bootstrap_tokens WHERE pool_id=$1", pool_id
            )
            await c.execute("DELETE FROM compute_pools WHERE id=$1", pool_id)
        if org_id is not None:
            await c.execute(
                "DELETE FROM organizations WHERE id=$1", str(org_id)
            )


async def test_purge_node_removes_all_residue_and_fails_deployment(pool):
    repo = InventoryRepository(pool)
    org_id, pool_id = await _seed_org_and_pool(pool)
    node_id = await _seed_node(pool, pool_id)
    job_id = await _seed_provisioning_job(pool, pool_id, node_id, org_id)
    await _seed_event(pool, pool_id, node_id)
    await _seed_event(pool, pool_id, node_id)
    token_id = await _seed_bootstrap_token(pool, pool_id, org_id, node_id)
    dep_id = await _seed_deployment(pool, pool_id, node_id, org_id,
                                    state="RUNNING")

    try:
        await repo.purge_node(node_id)

        async with pool.acquire() as c:
            assert await c.fetchval(
                "SELECT count(*) FROM compute_inventory WHERE id=$1", node_id
            ) == 0
            assert await c.fetchval(
                "SELECT count(*) FROM provisioning_jobs WHERE node_id=$1",
                node_id,
            ) == 0
            assert await c.fetchval(
                "SELECT count(*) FROM node_provisioning_events WHERE node_id=$1",
                node_id,
            ) == 0
            assert await c.fetchval(
                "SELECT count(*) FROM worker_bootstrap_tokens "
                "WHERE consumed_node_id=$1",
                node_id,
            ) == 0
            dep = await c.fetchrow(
                "SELECT state, target_node_id, endpoint, error_message "
                "FROM model_deployments WHERE deployment_id=$1",
                dep_id,
            )
        assert dep["state"] == "FAILED"
        assert dep["target_node_id"] is None
        assert dep["endpoint"] is None
        assert dep["error_message"] == "node deleted"
        # Reference the seeded token/job ids so an unused-var lint never hides
        # a seeding regression.
        assert job_id is not None and token_id is not None
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id, node_id=node_id,
                       deployment_ids=[dep_id])


@pytest.mark.parametrize("terminal_state", ["TERMINATED", "STOPPED", "FAILED"])
async def test_purge_node_does_not_clobber_terminal_deployment(
    pool, terminal_state
):
    """A deployment already in a terminal state keeps its recorded outcome
    (state / endpoint / error_message) — purge_node must NOT clobber it — but
    its target_node_id is detached (NULLed) so the inventory hard-delete is
    not blocked by the NO-ACTION model_deployments_target_node_id_fkey."""
    repo = InventoryRepository(pool)
    org_id, pool_id = await _seed_org_and_pool(pool)
    node_id = await _seed_node(pool, pool_id)
    dep_id = await _seed_deployment(pool, pool_id, node_id, org_id,
                                    state=terminal_state)
    try:
        await repo.purge_node(node_id)
        async with pool.acquire() as c:
            # The node itself is hard-deleted.
            assert await c.fetchval(
                "SELECT count(*) FROM compute_inventory WHERE id=$1", node_id
            ) == 0
            dep = await c.fetchrow(
                "SELECT state, target_node_id, endpoint "
                "FROM model_deployments WHERE deployment_id=$1",
                dep_id,
            )
        # The deployment keeps its terminal state + endpoint (not clobbered)
        # but is detached from the now-deleted node.
        assert dep["state"] == terminal_state
        assert dep["target_node_id"] is None
        assert dep["endpoint"] == "http://node:8080"
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id, node_id=node_id,
                       deployment_ids=[dep_id])


async def test_purge_node_no_residue_is_noop(pool):
    """A node with NO residue (no jobs/events/tokens/deployments) purges
    cleanly with no error; only the inventory row goes away."""
    repo = InventoryRepository(pool)
    org_id, pool_id = await _seed_org_and_pool(pool)
    node_id = await _seed_node(pool, pool_id)
    try:
        await repo.purge_node(node_id)
        async with pool.acquire() as c:
            assert await c.fetchval(
                "SELECT count(*) FROM compute_inventory WHERE id=$1", node_id
            ) == 0
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id, node_id=node_id)


async def test_purge_node_nonexistent_id_is_noop(pool):
    """purge_node on a never-existed node id raises nothing."""
    repo = InventoryRepository(pool)
    ghost = uuid4()
    await repo.purge_node(ghost)  # must not raise


async def test_mark_destroy_failed_stamps_metadata_keeps_state(pool):
    """mark_destroy_failed records destroy_failed/destroy_error on metadata
    WITHOUT flipping state — so a real pulumi-destroy failure surfaces to the
    dashboard while the job stays retryable and the row stays queryable."""
    import json
    repo = InventoryRepository(pool)
    org_id, pool_id = await _seed_org_and_pool(pool)
    node_id = await _seed_node(pool, pool_id, state="ready")
    try:
        await repo.mark_destroy_failed(
            node_id, "RuntimeError: api error in-use dependency",
        )
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT state, metadata FROM compute_inventory WHERE id=$1",
                node_id,
            )
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        # state untouched (NOT terminated) — the EC2 may still be running.
        assert row["state"] == "ready"
        assert meta.get("destroy_failed") is True
        assert "api error" in meta.get("destroy_error", "")
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id, node_id=node_id)


async def test_purge_node_uses_external_tx(pool):
    """When a caller passes tx, all the deletes/updates land in that tx and
    only persist after the caller commits."""
    repo = InventoryRepository(pool)
    org_id, pool_id = await _seed_org_and_pool(pool)
    node_id = await _seed_node(pool, pool_id)
    await _seed_provisioning_job(pool, pool_id, node_id, org_id)
    dep_id = await _seed_deployment(pool, pool_id, node_id, org_id,
                                    state="DEPLOYING")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await repo.purge_node(node_id, tx=conn)
                # Inside the tx, the row is already gone.
                assert await conn.fetchval(
                    "SELECT count(*) FROM compute_inventory WHERE id=$1",
                    node_id,
                ) == 0
        # After commit, it stays gone and the deploy is FAILED+unbound.
        async with pool.acquire() as c:
            assert await c.fetchval(
                "SELECT count(*) FROM compute_inventory WHERE id=$1", node_id
            ) == 0
            dep = await c.fetchrow(
                "SELECT state, target_node_id FROM model_deployments "
                "WHERE deployment_id=$1",
                dep_id,
            )
        assert dep["state"] == "FAILED"
        assert dep["target_node_id"] is None
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id, node_id=node_id,
                       deployment_ids=[dep_id])
