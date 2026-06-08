"""Real-PG tests for ComputePoolRepository PHASE-2 pool finalizer.

``finalize_pool_delete(pool_id)`` is the authoritative HARD-delete for a pool
once its LAST node has been purged: it deletes the pool's
``node_provisioning_events`` (no FK on pool_id) and ``worker_bootstrap_tokens``
(unconsumed pool tokens), then HARD-deletes the ``compute_pools`` row — which
fires the ON DELETE CASCADE FKs that point AT compute_pools (e.g.
``autoscaler_state``) and frees the UNIQUE(pool_name, owner_type, owner_id) so
a same-named pool can be re-created.

``count_live_inventory(pool_id)`` is the teardown-progress signal the
reconciler keys the finalizer off (a pool is only finalized once it returns 0).

``get_lifecycle_state(pool_id)`` reads lifecycle WITHOUT the is_active filter
(a pool mid-teardown is already soft-deleted).

These run against the throwaway test DB wired into the inferia-test container
(TEST_DATABASE_URL / INFERIA_TEST_DATABASE_URL). Each test seeds its own
org/pool and tears everything down so the shared DB stays clean.
"""
from __future__ import annotations
import os
import pytest
import pytest_asyncio
import asyncpg
from uuid import uuid4

from inferia.services.orchestration.repositories.pool_repo import (
    ComputePoolRepository,
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


async def _seed_org(p):
    org_id = uuid4()
    async with p.acquire() as c:
        await c.execute(
            "INSERT INTO organizations(id, name) VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING",
            str(org_id), f"test-org-{org_id}",
        )
    return org_id


async def _seed_pool(p, org_id, *, pool_name=None, lifecycle="terminating",
                     is_active=False):
    pool_id = uuid4()
    name = pool_name or f"p-{pool_id}"
    async with p.acquire() as c:
        await c.execute(
            """
            INSERT INTO compute_pools(
              id, pool_name, owner_type, owner_id, org_id, provider, pool_type,
              allowed_gpu_types, max_cost_per_hour, scheduling_policy,
              provider_pool_id, is_active, gpu_count, lifecycle_state
            )
            VALUES ($1, $2, 'organization', $3::text, $3::text, 'aws',
                    'cluster', ARRAY['t3.small']::text[], 10.0, '{}'::jsonb,
                    $4, $5, 4, $6)
            """,
            pool_id, name, str(org_id), f"placeholder:{pool_id}",
            is_active, lifecycle,
        )
    return pool_id, name


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


async def _seed_pool_event(p, pool_id):
    """A pool-scoped event row with NO node_id (orphan / pool-only)."""
    async with p.acquire() as c:
        return await c.fetchval(
            """
            INSERT INTO node_provisioning_events(pool_id, phase, status, message)
            VALUES ($1, 'preflight', 'running', 'pool-only')
            RETURNING id
            """,
            pool_id,
        )


async def _seed_pool_token(p, pool_id, org_id):
    """An UNconsumed pool bootstrap token (consumed_node_id NULL)."""
    token_id = uuid4()
    async with p.acquire() as c:
        await c.execute(
            """
            INSERT INTO worker_bootstrap_tokens(
              id, token_hash, pool_id, org_id, expires_at
            )
            VALUES ($1, $2, $3, $4, now() + interval '1 hour')
            """,
            token_id, f"hash-{token_id}", pool_id, str(org_id),
        )
    return token_id


async def _seed_autoscaler_state(p, pool_id):
    async with p.acquire() as c:
        await c.execute(
            "INSERT INTO autoscaler_state(pool_id, consecutive_failures) "
            "VALUES ($1, 3) ON CONFLICT (pool_id) DO NOTHING",
            pool_id,
        )


async def _cleanup(p, *, org_id=None, pool_id=None):
    async with p.acquire() as c:
        if pool_id is not None:
            await c.execute(
                "DELETE FROM node_provisioning_events WHERE pool_id=$1", pool_id
            )
            await c.execute(
                "DELETE FROM worker_bootstrap_tokens WHERE pool_id=$1", pool_id
            )
            await c.execute(
                "DELETE FROM autoscaler_state WHERE pool_id=$1", pool_id
            )
            await c.execute(
                "DELETE FROM compute_inventory WHERE pool_id=$1", pool_id
            )
            await c.execute("DELETE FROM compute_pools WHERE id=$1", pool_id)
        if org_id is not None:
            await c.execute("DELETE FROM organizations WHERE id=$1", str(org_id))


async def test_finalize_pool_delete_removes_pool_and_all_residue(pool):
    repo = ComputePoolRepository(pool)
    org_id = await _seed_org(pool)
    pool_id, _ = await _seed_pool(pool, org_id)  # terminating, no inventory
    ev_id = await _seed_pool_event(pool, pool_id)
    tok_id = await _seed_pool_token(pool, pool_id, org_id)
    await _seed_autoscaler_state(pool, pool_id)
    try:
        deleted = await repo.finalize_pool_delete(pool_id)
        assert deleted is True

        async with pool.acquire() as c:
            # The compute_pools row is GONE.
            assert await c.fetchval(
                "SELECT count(*) FROM compute_pools WHERE id=$1", pool_id
            ) == 0
            # Pool-scoped events GONE (no FK — explicit delete).
            assert await c.fetchval(
                "SELECT count(*) FROM node_provisioning_events WHERE pool_id=$1",
                pool_id,
            ) == 0
            # Unconsumed pool bootstrap tokens GONE.
            assert await c.fetchval(
                "SELECT count(*) FROM worker_bootstrap_tokens WHERE pool_id=$1",
                pool_id,
            ) == 0
            # autoscaler_state GONE via ON DELETE CASCADE on compute_pools.
            assert await c.fetchval(
                "SELECT count(*) FROM autoscaler_state WHERE pool_id=$1", pool_id
            ) == 0
        # Reference seeded ids so an unused-var lint never hides a regression.
        assert ev_id is not None and tok_id is not None
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id)


async def test_finalize_pool_delete_frees_unique_name(pool):
    """After finalize, a same-name pool (same owner) can be re-created — the
    UNIQUE(pool_name, owner_type, owner_id) was freed by the hard-delete."""
    repo = ComputePoolRepository(pool)
    org_id = await _seed_org(pool)
    pool_id, name = await _seed_pool(pool, org_id)
    new_pool_id = None
    try:
        await repo.finalize_pool_delete(pool_id)
        # Re-create a pool with the EXACT same (pool_name, owner) — must not
        # violate the unique constraint now that the old row is hard-deleted.
        new_pool_id, _ = await _seed_pool(pool, org_id, pool_name=name,
                                          lifecycle="running", is_active=True)
        async with pool.acquire() as c:
            assert await c.fetchval(
                "SELECT count(*) FROM compute_pools WHERE id=$1", new_pool_id
            ) == 1
    finally:
        await _cleanup(pool, pool_id=new_pool_id)
        await _cleanup(pool, org_id=org_id, pool_id=pool_id)


async def test_finalize_pool_delete_idempotent_on_missing_pool(pool):
    """A pool that's already gone → returns False, raises nothing."""
    repo = ComputePoolRepository(pool)
    ghost = uuid4()
    assert await repo.finalize_pool_delete(ghost) is False


async def test_finalize_pool_delete_uses_external_tx(pool):
    """When a caller passes tx, the deletes land in that tx and only persist
    after the caller commits."""
    repo = ComputePoolRepository(pool)
    org_id = await _seed_org(pool)
    pool_id, _ = await _seed_pool(pool, org_id)
    await _seed_pool_token(pool, pool_id, org_id)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                deleted = await repo.finalize_pool_delete(pool_id, tx=conn)
                assert deleted is True
                # Inside the tx the row is already gone.
                assert await conn.fetchval(
                    "SELECT count(*) FROM compute_pools WHERE id=$1", pool_id
                ) == 0
        # After commit it stays gone.
        async with pool.acquire() as c:
            assert await c.fetchval(
                "SELECT count(*) FROM compute_pools WHERE id=$1", pool_id
            ) == 0
            assert await c.fetchval(
                "SELECT count(*) FROM worker_bootstrap_tokens WHERE pool_id=$1",
                pool_id,
            ) == 0
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id)


async def test_count_live_inventory_counts_every_state(pool):
    """count_live_inventory counts EVERY inventory row regardless of state —
    it's the 'last node purged?' signal, NOT the scheduler capacity gate."""
    repo = ComputePoolRepository(pool)
    org_id = await _seed_org(pool)
    pool_id, _ = await _seed_pool(pool, org_id)
    try:
        assert await repo.count_live_inventory(pool_id) == 0
        n1 = await _seed_node(pool, pool_id, state="ready")
        assert await repo.count_live_inventory(pool_id) == 1
        # Even a 'terminated' row still counts here (it has not been purged).
        await _seed_node(pool, pool_id, state="terminated")
        assert await repo.count_live_inventory(pool_id) == 2
        # Hard-deleting (purge) the row drops the count.
        async with pool.acquire() as c:
            await c.execute("DELETE FROM compute_inventory WHERE id=$1", n1)
        assert await repo.count_live_inventory(pool_id) == 1
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id)


async def test_finalize_pool_delete_cascades_stray_deployment(pool):
    """A terminal model_deployments row still referencing the pool when the
    finalizer runs cascades away cleanly via the compute_pools ON DELETE
    CASCADE FK (deployment → inference_logs CASCADE, policies/api_keys SET
    NULL). Proves the hard-delete never trips a FK violation and leaves ZERO
    deployment residue."""
    repo = ComputePoolRepository(pool)
    org_id = await _seed_org(pool)
    pool_id, _ = await _seed_pool(pool, org_id)
    deploy_id = uuid4()
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO model_deployments(
                   deployment_id, model_name, replicas, gpu_per_replica,
                   pool_id, target_pool_id, state, org_id)
               VALUES($1,$2,1,1,$3,$3,'TERMINATED',$4)""",
            deploy_id, f"m-{deploy_id}", pool_id, str(org_id),
        )
        await c.execute(
            "INSERT INTO inference_logs(id, deployment_id, user_id, model) "
            "VALUES($1,$2,'u','m')",
            f"log-{deploy_id}", deploy_id,
        )
        await c.execute(
            "INSERT INTO policies(id, policy_type, config_json, org_id, "
            "deployment_id) VALUES($1,'rate_limit','{}'::json,$2,$3)",
            f"pol-{deploy_id}", str(org_id), deploy_id,
        )
        await c.execute(
            "INSERT INTO api_keys(id, name, key_hash, prefix, org_id, "
            "deployment_id) VALUES($1,'k',$2,$3,$4,$5)",
            f"ak-{deploy_id}", f"hash-{deploy_id}", f"pre{str(deploy_id)[:6]}",
            str(org_id), deploy_id,
        )
    try:
        deleted = await repo.finalize_pool_delete(pool_id)
        assert deleted is True
        async with pool.acquire() as c:
            assert await c.fetchval(
                "SELECT count(*) FROM compute_pools WHERE id=$1", pool_id
            ) == 0
            # Deployment cascaded away with the pool.
            assert await c.fetchval(
                "SELECT count(*) FROM model_deployments WHERE deployment_id=$1",
                deploy_id,
            ) == 0
            # inference_logs cascaded; policies/api_keys detached (SET NULL).
            assert await c.fetchval(
                "SELECT count(*) FROM inference_logs WHERE deployment_id=$1",
                deploy_id,
            ) == 0
            assert await c.fetchval(
                "SELECT deployment_id FROM policies WHERE id=$1",
                f"pol-{deploy_id}",
            ) is None
            assert await c.fetchval(
                "SELECT deployment_id FROM api_keys WHERE id=$1",
                f"ak-{deploy_id}",
            ) is None
    finally:
        async with pool.acquire() as c:
            await c.execute("DELETE FROM policies WHERE id=$1", f"pol-{deploy_id}")
            await c.execute("DELETE FROM api_keys WHERE id=$1", f"ak-{deploy_id}")
            await c.execute(
                "DELETE FROM inference_logs WHERE deployment_id=$1", deploy_id
            )
            await c.execute(
                "DELETE FROM model_deployments WHERE deployment_id=$1", deploy_id
            )
        await _cleanup(pool, org_id=org_id, pool_id=pool_id)


async def test_finalize_pool_delete_detaches_divergent_target_pool_id(pool):
    """A deployment whose ``target_pool_id`` points at the pool being finalized
    but whose ``pool_id`` lives in a DIFFERENT (surviving) pool must NOT block
    the hard-delete.

    ``model_deployments.pool_id`` is ON DELETE CASCADE, so a row with
    ``pool_id == this`` cascades away. But ``target_pool_id`` is ON DELETE NO
    ACTION (added by the 20260530 migration with no ON DELETE clause). When a
    deployment is re-placed so ``pool_id`` diverges from ``target_pool_id``, the
    NO-ACTION FK on ``target_pool_id`` would raise ForeignKeyViolation on the
    ``DELETE FROM compute_pools`` → the whole finalize rolls back → the pool is
    stuck 'terminating' forever. The finalizer NULLs ``target_pool_id`` first so
    the divergent row survives (it belongs to another live pool) with a detached
    target.
    """
    repo = ComputePoolRepository(pool)
    org_id = await _seed_org(pool)
    # The pool being finalized (terminating, zero inventory).
    pool_id, _ = await _seed_pool(pool, org_id)
    # A second, LIVE pool that owns the divergent deployment.
    other_pool_id, _ = await _seed_pool(
        pool, org_id, lifecycle="running", is_active=True,
    )
    deploy_id = uuid4()
    async with pool.acquire() as c:
        # pool_id -> other (live) pool; target_pool_id -> pool being deleted.
        await c.execute(
            """INSERT INTO model_deployments(
                   deployment_id, model_name, replicas, gpu_per_replica,
                   pool_id, target_pool_id, state, org_id)
               VALUES($1,$2,1,1,$3,$4,'RUNNING',$5)""",
            deploy_id, f"m-{deploy_id}", other_pool_id, pool_id, str(org_id),
        )
    try:
        # Without the NULL-out this raises asyncpg.ForeignKeyViolationError.
        deleted = await repo.finalize_pool_delete(pool_id)
        assert deleted is True
        async with pool.acquire() as c:
            # The finalized pool row is gone.
            assert await c.fetchval(
                "SELECT count(*) FROM compute_pools WHERE id=$1", pool_id
            ) == 0
            # The divergent deployment STILL EXISTS (it belongs to other_pool).
            row = await c.fetchrow(
                "SELECT pool_id, target_pool_id FROM model_deployments "
                "WHERE deployment_id=$1",
                deploy_id,
            )
            assert row is not None
            assert row["pool_id"] == other_pool_id
            # ...and its target_pool_id has been detached (NULLed).
            assert row["target_pool_id"] is None
    finally:
        async with pool.acquire() as c:
            await c.execute(
                "DELETE FROM model_deployments WHERE deployment_id=$1", deploy_id
            )
        await _cleanup(pool, pool_id=other_pool_id)
        await _cleanup(pool, org_id=org_id, pool_id=pool_id)


async def test_get_lifecycle_state_reads_soft_deleted_pool(pool):
    """get_lifecycle_state returns 'terminating' even for an is_active=FALSE
    pool (the public get() filters those out; the finalizer must still see
    the terminating state to decide whether to hard-delete)."""
    repo = ComputePoolRepository(pool)
    org_id = await _seed_org(pool)
    pool_id, _ = await _seed_pool(pool, org_id, lifecycle="terminating",
                                  is_active=False)
    try:
        assert await repo.get_lifecycle_state(pool_id) == "terminating"
        # A nonexistent pool returns None.
        assert await repo.get_lifecycle_state(uuid4()) is None
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id)
