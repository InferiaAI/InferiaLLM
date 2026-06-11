"""Real-PG tests for the TerminationReaper self-healing backstop.

The reaper periodically catches teardown residue the deterministic paths
missed:

  * NODES flagged ``metadata.terminating='true'`` with no live ``cancelling``
    provisioning_job → re-arm via ``force_cancel`` when a re-armable job
    exists, else sweep+purge directly.
  * POOLS in ``lifecycle_state='terminating'`` with ZERO inventory rows →
    ``finalize_pool_delete`` (hard-delete + residue cleanup).

These run against the throwaway test DB wired into the inferia-test container
(TEST_DATABASE_URL / INFERIA_TEST_DATABASE_URL). Each test seeds its own
org/pool/node and tears everything down so the shared DB stays clean. The
boto3 orphan sweep is patched out (no AWS calls in CI).
"""
from __future__ import annotations

import os
from unittest.mock import patch
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from services.orchestration.repositories.inventory_repo import (
    InventoryRepository,
)
from services.orchestration.repositories.pool_repo import (
    ComputePoolRepository,
)
from services.orchestration.provisioning_state_machine.jobs.repository import (
    ProvisioningJobRepository,
)
from services.orchestration.provisioning_state_machine.reconciler.reaper import (
    TerminationReaper,
)

pytestmark = pytest.mark.asyncio

_SWEEP_PATH = (
    "services.orchestration.adapter_engine."
    "aws_orphan_sweep.sweep_node_instances"
)


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


def _reaper(pool, *, grace_s: float = 0.0) -> TerminationReaper:
    return TerminationReaper(
        db=pool,
        inventory_repo=InventoryRepository(pool),
        pool_repo=ComputePoolRepository(pool),
        jobs_repo=ProvisioningJobRepository(pool),
        interval_s=60.0,
        grace_s=grace_s,
    )


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_org(p):
    org_id = uuid4()
    async with p.acquire() as c:
        await c.execute(
            "INSERT INTO organizations(id, name) VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING",
            str(org_id), f"test-org-{org_id}",
        )
    return org_id


async def _seed_pool(p, org_id, *, lifecycle="terminating", is_active=False):
    pool_id = uuid4()
    async with p.acquire() as c:
        await c.execute(
            """
            INSERT INTO compute_pools(
              id, pool_name, owner_type, owner_id, org_id, provider, pool_type,
              allowed_gpu_types, max_cost_per_hour, scheduling_policy,
              provider_pool_id, is_active, gpu_count, lifecycle_state,
              region_constraint
            )
            VALUES ($1, $2, 'organization', $3::text, $3::text, 'aws',
                    'cluster', ARRAY['t3.small']::text[], 10.0, '{}'::jsonb,
                    $4, $5, 4, $6, ARRAY['us-east-1']::text[])
            """,
            pool_id, f"p-{pool_id}", str(org_id), f"placeholder:{pool_id}",
            is_active, lifecycle,
        )
    return pool_id


async def _seed_node(p, pool_id, *, terminating=True, state="ready",
                     terminating_at_past=True):
    """Insert a compute_inventory row.

    terminating=True stamps metadata.terminating + a terminating_at far in
    the PAST (so the grace gate passes immediately). terminating_at_past=False
    stamps it at now() (so the grace gate holds the node back).
    """
    node_id = uuid4()
    if terminating:
        ts = (
            "(now() - interval '1 hour')::text"
            if terminating_at_past
            else "now()::text"
        )
        meta_sql = (
            "jsonb_build_object('terminating', true, "
            f"'terminating_at', {ts})"
        )
    else:
        meta_sql = "'{}'::jsonb"
    async with p.acquire() as c:
        await c.execute(
            f"""
            INSERT INTO compute_inventory(
              id, pool_id, provider, provider_instance_id, hostname,
              node_name, agent_kind, gpu_total, gpu_allocated, vcpu_total,
              vcpu_allocated, ram_gb_total, ram_gb_allocated, state, metadata
            )
            VALUES ($1, $2, 'aws', $3, 'h', $4, 'worker',
                    4, 0, 0, 0, 0, 0, $5, {meta_sql})
            """,
            node_id, pool_id, str(node_id), f"node-{node_id}", state,
        )
    return node_id


async def _seed_job(p, node_id, pool_id, org_id, *, phase):
    job_id = uuid4()
    async with p.acquire() as c:
        await c.execute(
            """
            INSERT INTO provisioning_jobs(
              id, node_id, pool_id, org_id, provider, spec, phase
            )
            VALUES ($1, $2, $3, $4::text, 'aws',
                    '{"region": "us-east-1"}'::jsonb, $5)
            """,
            job_id, node_id, pool_id, str(org_id), phase,
        )
    return job_id


async def _node_exists(p, node_id) -> bool:
    async with p.acquire() as c:
        return bool(await c.fetchval(
            "SELECT 1 FROM compute_inventory WHERE id=$1", node_id
        ))


async def _job_phase(p, node_id):
    async with p.acquire() as c:
        return await c.fetchval(
            "SELECT phase FROM provisioning_jobs WHERE node_id=$1", node_id
        )


async def _pool_exists(p, pool_id) -> bool:
    async with p.acquire() as c:
        return bool(await c.fetchval(
            "SELECT 1 FROM compute_pools WHERE id=$1", pool_id
        ))


async def _cleanup(p, *, org_id=None, pool_id=None, node_id=None):
    async with p.acquire() as c:
        if node_id is not None:
            await c.execute(
                "DELETE FROM provisioning_jobs WHERE node_id=$1", node_id
            )
            await c.execute(
                "DELETE FROM compute_inventory WHERE id=$1", node_id
            )
        if pool_id is not None:
            await c.execute(
                "DELETE FROM provisioning_jobs WHERE pool_id=$1", pool_id
            )
            await c.execute(
                "DELETE FROM compute_inventory WHERE pool_id=$1", pool_id
            )
            await c.execute(
                "DELETE FROM node_provisioning_events WHERE pool_id=$1", pool_id
            )
            await c.execute(
                "DELETE FROM worker_bootstrap_tokens WHERE pool_id=$1", pool_id
            )
            await c.execute("DELETE FROM compute_pools WHERE id=$1", pool_id)
        if org_id is not None:
            await c.execute("DELETE FROM organizations WHERE id=$1", str(org_id))


# ---------------------------------------------------------------------------
# NODE reaping
# ---------------------------------------------------------------------------


async def test_reaper_purges_stuck_node_with_no_job(pool):
    """A node flagged terminating with NO provisioning_job → one tick sweeps
    + purges it (the inventory row is gone)."""
    org_id = await _seed_org(pool)
    pool_id = await _seed_pool(pool, org_id, lifecycle="running", is_active=True)
    node_id = await _seed_node(pool, pool_id, terminating=True)
    try:
        with patch(_SWEEP_PATH, return_value=[]) as mock_sweep:
            await _reaper(pool).tick_once()
        assert not await _node_exists(pool, node_id)
        # The boto3 sweep backstop ran (provider=aws, region resolvable).
        mock_sweep.assert_called_once()
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id, node_id=node_id)


async def test_reaper_rearms_stuck_node_with_live_job(pool):
    """A node flagged terminating WITH a re-armable (ready) job → one tick
    flips that job to 'cancelling' (re-arm) and leaves the row in place for
    the CancelHandler to destroy. No sweep/purge directly."""
    org_id = await _seed_org(pool)
    pool_id = await _seed_pool(pool, org_id, lifecycle="running", is_active=True)
    node_id = await _seed_node(pool, pool_id, terminating=True)
    await _seed_job(pool, node_id, pool_id, org_id, phase="ready")
    try:
        with patch(_SWEEP_PATH, return_value=[]) as mock_sweep:
            await _reaper(pool).tick_once()
        # Job re-armed to cancelling; node row still present (CancelHandler
        # will destroy + purge it).
        assert await _job_phase(pool, node_id) == "cancelling"
        assert await _node_exists(pool, node_id)
        mock_sweep.assert_not_called()
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id, node_id=node_id)


async def test_reaper_ignores_node_with_cancelling_job(pool):
    """A node flagged terminating whose job is ALREADY cancelling (destroy in
    flight) must be left completely alone — no purge, no re-flip."""
    org_id = await _seed_org(pool)
    pool_id = await _seed_pool(pool, org_id, lifecycle="running", is_active=True)
    node_id = await _seed_node(pool, pool_id, terminating=True)
    await _seed_job(pool, node_id, pool_id, org_id, phase="cancelling")
    try:
        with patch(_SWEEP_PATH, return_value=[]) as mock_sweep:
            await _reaper(pool).tick_once()
        assert await _node_exists(pool, node_id)
        assert await _job_phase(pool, node_id) == "cancelling"
        mock_sweep.assert_not_called()
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id, node_id=node_id)


async def test_reaper_purges_stuck_node_with_terminated_job(pool):
    """A node flagged terminating whose job already reached 'terminated' (the
    destroy ran but the purge was missed) → force_cancel flips nothing, so the
    reaper falls back to sweep+purge directly."""
    org_id = await _seed_org(pool)
    pool_id = await _seed_pool(pool, org_id, lifecycle="running", is_active=True)
    node_id = await _seed_node(pool, pool_id, terminating=True)
    await _seed_job(pool, node_id, pool_id, org_id, phase="terminated")
    try:
        with patch(_SWEEP_PATH, return_value=["i-abc"]) as mock_sweep:
            await _reaper(pool).tick_once()
        assert not await _node_exists(pool, node_id)
        mock_sweep.assert_called_once()
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id, node_id=node_id)


async def test_reaper_leaves_non_terminating_node_untouched(pool):
    """A node WITHOUT the terminating flag is never touched by the reaper."""
    org_id = await _seed_org(pool)
    pool_id = await _seed_pool(pool, org_id, lifecycle="running", is_active=True)
    node_id = await _seed_node(pool, pool_id, terminating=False, state="ready")
    try:
        with patch(_SWEEP_PATH, return_value=[]) as mock_sweep:
            await _reaper(pool).tick_once()
        assert await _node_exists(pool, node_id)
        mock_sweep.assert_not_called()
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id, node_id=node_id)


async def test_reaper_respects_grace_period(pool):
    """A node flagged terminating only just now (terminating_at=now) is left
    alone while inside the grace window."""
    org_id = await _seed_org(pool)
    pool_id = await _seed_pool(pool, org_id, lifecycle="running", is_active=True)
    node_id = await _seed_node(
        pool, pool_id, terminating=True, terminating_at_past=False,
    )
    try:
        with patch(_SWEEP_PATH, return_value=[]) as mock_sweep:
            # grace_s large enough that a just-flagged node is excluded.
            await _reaper(pool, grace_s=3600.0).tick_once()
        assert await _node_exists(pool, node_id)
        mock_sweep.assert_not_called()
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id, node_id=node_id)


# ---------------------------------------------------------------------------
# POOL reaping
# ---------------------------------------------------------------------------


async def test_reaper_finalizes_empty_terminating_pool(pool):
    """A terminating pool with ZERO inventory rows → one tick hard-deletes the
    pool row."""
    org_id = await _seed_org(pool)
    pool_id = await _seed_pool(pool, org_id, lifecycle="terminating",
                               is_active=False)
    try:
        with patch(_SWEEP_PATH, return_value=[]):
            await _reaper(pool).tick_once()
        assert not await _pool_exists(pool, pool_id)
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id)


async def test_reaper_leaves_terminating_pool_with_live_node(pool):
    """A terminating pool that STILL has an inventory row is left alone (its
    last node hasn't been purged yet)."""
    org_id = await _seed_org(pool)
    pool_id = await _seed_pool(pool, org_id, lifecycle="terminating",
                               is_active=False)
    # A node WITHOUT the terminating flag — the node reaper won't touch it,
    # and its presence must keep the pool from being finalized.
    node_id = await _seed_node(pool, pool_id, terminating=False, state="ready")
    try:
        with patch(_SWEEP_PATH, return_value=[]):
            await _reaper(pool).tick_once()
        assert await _pool_exists(pool, pool_id)
        assert await _node_exists(pool, node_id)
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id, node_id=node_id)


async def test_reaper_leaves_running_pool_untouched(pool):
    """A pool that is NOT terminating (lifecycle=running) is never finalized,
    even with zero inventory."""
    org_id = await _seed_org(pool)
    pool_id = await _seed_pool(pool, org_id, lifecycle="running", is_active=True)
    try:
        with patch(_SWEEP_PATH, return_value=[]):
            await _reaper(pool).tick_once()
        assert await _pool_exists(pool, pool_id)
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id)


# ---------------------------------------------------------------------------
# Idempotency / end-to-end
# ---------------------------------------------------------------------------


async def test_reaper_is_idempotent(pool):
    """Two consecutive ticks: the first purges the stuck node + finalizes the
    empty pool; the second is a clean no-op (raises nothing)."""
    org_id = await _seed_org(pool)
    # An empty terminating pool to finalize...
    empty_pool_id = await _seed_pool(pool, org_id, lifecycle="terminating",
                                     is_active=False)
    # ...and a separate running pool holding a stuck-terminating node.
    host_pool_id = await _seed_pool(pool, org_id, lifecycle="running",
                                    is_active=True)
    node_id = await _seed_node(pool, host_pool_id, terminating=True)
    try:
        reaper = _reaper(pool)
        with patch(_SWEEP_PATH, return_value=[]):
            await reaper.tick_once()
            # First tick did the work.
            assert not await _pool_exists(pool, empty_pool_id)
            assert not await _node_exists(pool, node_id)
            # Second tick — nothing left to do, must not raise.
            await reaper.tick_once()
        assert not await _pool_exists(pool, empty_pool_id)
        assert not await _node_exists(pool, node_id)
    finally:
        await _cleanup(pool, pool_id=host_pool_id, node_id=node_id)
        await _cleanup(pool, org_id=org_id, pool_id=empty_pool_id)


# ---------------------------------------------------------------------------
# B2: clear_terminating_node helper
# ---------------------------------------------------------------------------


async def test_clear_terminating_node_strips_flags(pool):
    """InventoryRepository.clear_terminating_node removes both terminating and
    terminating_at from metadata, idempotently."""
    org_id = await _seed_org(pool)
    pool_id = await _seed_pool(pool, org_id, lifecycle="running", is_active=True)
    node_id = await _seed_node(pool, pool_id, terminating=True)
    repo = InventoryRepository(pool)
    try:
        # Pre-condition: terminating flag present.
        async with pool.acquire() as c:
            assert await c.fetchval(
                "SELECT metadata->>'terminating' FROM compute_inventory "
                "WHERE id=$1", node_id,
            ) == "true"
        await repo.clear_terminating_node(node_id=node_id)
        async with pool.acquire() as c:
            row = await c.fetchval(
                "SELECT metadata FROM compute_inventory WHERE id=$1", node_id,
            )
        import json as _json
        meta = _json.loads(row) if isinstance(row, str) else (row or {})
        assert "terminating" not in meta
        assert "terminating_at" not in meta
        # Idempotent second call raises nothing.
        await repo.clear_terminating_node(node_id=node_id)
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id, node_id=node_id)


# ---------------------------------------------------------------------------
# BUILDER reaping
# ---------------------------------------------------------------------------

_RESOLVE_CREDS_PATH = (
    "services.orchestration.adapter_engine."
    "aws_orphan_sweep.resolve_sweep_aws_env"
)
_SWEEP_BUILDERS_PATH = (
    "services.orchestration.adapter_engine."
    "aws_orphan_sweep.sweep_stale_builders"
)

_DUMMY_AWS_ENV = {
    "AWS_ACCESS_KEY_ID": "test-key-id",
    "AWS_SECRET_ACCESS_KEY": "test-secret-key",
}


async def test_reaper_sweeps_stale_builders(pool):
    """A tick with an active AWS pool having region_constraint={us-east-1}
    causes sweep_stale_builders to be called for us-east-1."""
    from unittest.mock import MagicMock, AsyncMock

    org_id = await _seed_org(pool)
    # _seed_pool already seeds region_constraint=ARRAY['us-east-1'] by default.
    pool_id = await _seed_pool(pool, org_id, lifecycle="running", is_active=True)
    try:
        reaper = _reaper(pool)
        sweep_mock = MagicMock(return_value=[])
        with patch(_SWEEP_BUILDERS_PATH, sweep_mock), \
             patch(_RESOLVE_CREDS_PATH, new=AsyncMock(return_value=_DUMMY_AWS_ENV)):
            await reaper._reap_stale_builders()
        assert sweep_mock.called, "sweep_stale_builders was not called"
        regions = {call.args[0] for call in sweep_mock.call_args_list}
        assert "us-east-1" in regions, (
            f"us-east-1 not in swept regions: {regions}"
        )
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id)


async def test_reaper_sweeps_stale_builders_from_node_region(pool):
    """A tick where a node's metadata carries a region also drives the builder
    sweep for that region — so a pool-less node scenario is covered."""
    from unittest.mock import MagicMock, AsyncMock

    org_id = await _seed_org(pool)
    # Use a pool with no region_constraint (pass a different lifecycle so the
    # pool sweep doesn't accidentally interact), and plant the region in the
    # node's metadata instead.
    pool_id = await _seed_pool(pool, org_id, lifecycle="running", is_active=True)
    node_id = await _seed_node(pool, pool_id, terminating=False, state="ready")
    # Stamp metadata.region on the node.
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE compute_inventory SET metadata = metadata || $1::jsonb "
            "WHERE id = $2",
            '{"region": "eu-west-1"}', node_id,
        )
    try:
        reaper = _reaper(pool)
        sweep_mock = MagicMock(return_value=[])
        with patch(_SWEEP_BUILDERS_PATH, sweep_mock), \
             patch(_RESOLVE_CREDS_PATH, new=AsyncMock(return_value=_DUMMY_AWS_ENV)):
            await reaper._reap_stale_builders()
        assert sweep_mock.called, "sweep_stale_builders was not called"
        regions = {call.args[0] for call in sweep_mock.call_args_list}
        assert "eu-west-1" in regions, (
            f"eu-west-1 not in swept regions: {regions}"
        )
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id, node_id=node_id)


async def test_reaper_skips_builder_sweep_when_no_regions(pool):
    """When there are no AWS pools or nodes at all the builder sweep does not
    call sweep_stale_builders (nothing to reclaim)."""
    from unittest.mock import MagicMock, AsyncMock

    reaper = _reaper(pool)
    sweep_mock = MagicMock(return_value=[])
    # Ensure _aws_regions_in_use returns [] by NOT seeding any rows, and
    # shortcircuiting resolve_sweep_aws_env so creds are irrelevant.
    with patch(_SWEEP_BUILDERS_PATH, sweep_mock), \
         patch(_RESOLVE_CREDS_PATH, new=AsyncMock(return_value=_DUMMY_AWS_ENV)):
        # Patch regions helper to guarantee empty regardless of DB state.
        from unittest.mock import patch as _patch, AsyncMock as _AsyncMock
        with _patch.object(reaper, "_aws_regions_in_use", new=_AsyncMock(return_value=[])):
            await reaper._reap_stale_builders()
    sweep_mock.assert_not_called()


async def test_reaper_builder_sweep_survives_cred_resolution_failure(pool):
    """If resolve_sweep_aws_env raises, _reap_stale_builders logs and returns
    without calling sweep_stale_builders (best-effort contract)."""
    from unittest.mock import MagicMock, AsyncMock

    org_id = await _seed_org(pool)
    pool_id = await _seed_pool(pool, org_id, lifecycle="running", is_active=True)
    try:
        reaper = _reaper(pool)
        sweep_mock = MagicMock(return_value=[])
        failing_creds = AsyncMock(side_effect=RuntimeError("DB down"))
        with patch(_SWEEP_BUILDERS_PATH, sweep_mock), \
             patch(_RESOLVE_CREDS_PATH, new=failing_creds):
            # Must not raise.
            await reaper._reap_stale_builders()
        sweep_mock.assert_not_called()
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id)


async def test_reaper_builder_sweep_continues_after_per_region_failure(pool):
    """If sweep_stale_builders raises for one region, _reap_stale_builders
    continues to the next region without propagating the error."""
    from unittest.mock import MagicMock, AsyncMock, call

    org_id = await _seed_org(pool)
    # Seed a pool with us-east-1 (from _seed_pool default).
    pool_id = await _seed_pool(pool, org_id, lifecycle="running", is_active=True)
    # Add a node with a second region so we have two distinct regions to sweep.
    node_id = await _seed_node(pool, pool_id, terminating=False, state="ready")
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE compute_inventory SET metadata = metadata || $1::jsonb "
            "WHERE id = $2",
            '{"region": "ap-southeast-1"}', node_id,
        )
    try:
        reaper = _reaper(pool)
        call_count = 0

        def _sweep_side_effect(region, aws_env):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient AWS error")
            return []

        sweep_mock = MagicMock(side_effect=_sweep_side_effect)
        with patch(_SWEEP_BUILDERS_PATH, sweep_mock), \
             patch(_RESOLVE_CREDS_PATH, new=AsyncMock(return_value=_DUMMY_AWS_ENV)):
            # Must not raise; second region still attempted.
            await reaper._reap_stale_builders()
        # Both regions were attempted despite the first raising.
        assert sweep_mock.call_count == 2
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id, node_id=node_id)


async def test_reaper_tick_once_calls_builder_sweep(pool):
    """tick_once drives all three sweeps including the builder sweep."""
    from unittest.mock import MagicMock, AsyncMock

    org_id = await _seed_org(pool)
    pool_id = await _seed_pool(pool, org_id, lifecycle="running", is_active=True)
    try:
        reaper = _reaper(pool)
        sweep_mock = MagicMock(return_value=[])
        with patch(_SWEEP_PATH, return_value=[]), \
             patch(_SWEEP_BUILDERS_PATH, sweep_mock), \
             patch(_RESOLVE_CREDS_PATH, new=AsyncMock(return_value=_DUMMY_AWS_ENV)):
            await reaper.tick_once()
        assert sweep_mock.called, "sweep_stale_builders not reached via tick_once"
    finally:
        await _cleanup(pool, org_id=org_id, pool_id=pool_id)
