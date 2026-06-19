"""DB integration tests for InventoryRepository.finalize_direct_node.

These tests require a throwaway Postgres reachable at TEST_DATABASE_URL
(default: postgresql://inferia:inferia@localhost:5432/inferia_test).

Run with:
    docker run --rm --entrypoint python \\
        -e PYTHONPATH=/app/src \\
        -v /storage/intern/hooman/work/InferiaLLM:/app \\
        -w /app inferia-test-img:latest \\
        -m pytest src/tests/orchestration/repositories/test_inventory_finalize.py -q
"""
from __future__ import annotations

import json
import logging
import os

import asyncpg
import pytest
import pytest_asyncio
from uuid import uuid4

from orchestration.repositories.inventory_repo import InventoryRepository

pytestmark = pytest.mark.asyncio

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fixtures — mirrors test_inventory_repo_gpu_refcount.py exactly
# ---------------------------------------------------------------------------

_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://inferia:inferia@localhost:5432/inferia_test",
)


@pytest_asyncio.fixture
async def pool():
    """Real asyncpg pool connected to the test database."""
    p = await asyncpg.create_pool(dsn=_DSN, min_size=1, max_size=4)
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Helpers — reuse the exact seed pattern from sibling tests
# ---------------------------------------------------------------------------

async def _seed_org_and_pool(p, *, gpu_count=4, provider="nosana"):
    """Seed an org and pool row; return (org_id, pool_id) as UUIDs.

    Each call generates fresh UUIDs so tests never collide, and the returned
    IDs can be passed to _cleanup_org_and_pool in fixture teardown.
    """
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
            VALUES ($1, $2, 'organization', $3::text, $4, 'cluster',
                    ARRAY['gpu']::text[], 10.0, '{}'::jsonb,
                    $5, true, $6, 'running')
            """,
            pool_id, f"p-{pool_id}", str(org_id),
            provider, f"placeholder:{pool_id}", gpu_count,
        )
    return org_id, pool_id


async def _cleanup_org_and_pool(p, org_id, pool_id):
    """Delete seeded rows in FK-safe order (inventory → pool → org).

    Safe to call even if some rows were never inserted or were already removed.
    Each step is wrapped individually so a partial failure still attempts the rest.
    """
    async with p.acquire() as c:
        try:
            await c.execute(
                "DELETE FROM compute_inventory WHERE pool_id = $1",
                pool_id,
            )
        except asyncpg.PostgresError as e:
            logger.warning("cleanup: failed to delete compute_inventory for pool %s: %s", pool_id, e)
        try:
            await c.execute(
                "DELETE FROM compute_pools WHERE id = $1",
                pool_id,
            )
        except asyncpg.PostgresError as e:
            logger.warning("cleanup: failed to delete compute_pools for pool %s: %s", pool_id, e)
        try:
            await c.execute(
                "DELETE FROM organizations WHERE id = $1",
                str(org_id),
            )
        except asyncpg.PostgresError as e:
            logger.warning("cleanup: failed to delete organizations for org %s: %s", org_id, e)


def _run_id() -> str:
    """Return a short unique suffix for each test invocation.

    Appending this to provider_instance_id ensures the unique constraint
    (provider, provider_instance_id) never collides across runs, even if a
    previous run's cleanup was skipped due to an aborted process.
    """
    return str(uuid4())[:8]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_finalize_direct_node_marks_row_ready(pool):
    """finalize_direct_node fills in placeholder fields and flips state to ready."""
    repo = InventoryRepository(pool)
    org_id, pool_id = await _seed_org_and_pool(pool, provider="nosana", gpu_count=1)
    instance_id = f"nosana-job-abc-{_run_id()}"
    try:
        # Step 1: create the placeholder
        node_id = await repo.create_placeholder(
            pool_id=pool_id, gpu_total=1, initial_alloc=1
        )

        # Step 2: finalize in place
        result = await repo.finalize_direct_node(
            node_id=node_id,
            provider_instance_id=instance_id,
            hostname="https://hash.node.k8s.prd.nos.ci",
            gpu_total=1,
            vcpu_total=8,
            ram_gb_total=32,
            node_class="gpu",
            metadata={"market": "nvidia-4090"},
            expose_url="https://hash.node.k8s.prd.nos.ci",
        )
        assert result is True, "finalize_direct_node should return True on first finalize"

        # Step 3: fetch and assert
        row = await repo.get_node_by_id(node_id)
        assert row is not None, "node row not found after finalize"
        assert row["state"] == "ready"
        assert row["provider_instance_id"] == instance_id
        assert row["hostname"] == "https://hash.node.k8s.prd.nos.ci"
        assert row.get("expose_url") == "https://hash.node.k8s.prd.nos.ci", (
            f"expose_url mismatch: {row.get('expose_url')!r}"
        )
        assert row["vcpu_total"] == 8
        assert row["ram_gb_total"] == 32

        # metadata round-trip
        meta = row.get("metadata") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert meta.get("market") == "nvidia-4090"
    finally:
        await _cleanup_org_and_pool(pool, org_id, pool_id)


async def test_finalize_direct_node_no_duplicate_row(pool):
    """finalize_direct_node must update IN PLACE — no new inventory row."""
    repo = InventoryRepository(pool)
    org_id, pool_id = await _seed_org_and_pool(pool, provider="nosana", gpu_count=1)
    instance_id = f"nosana-job-xyz-{_run_id()}"
    try:
        node_id = await repo.create_placeholder(
            pool_id=pool_id, gpu_total=1, initial_alloc=1
        )

        result = await repo.finalize_direct_node(
            node_id=node_id,
            provider_instance_id=instance_id,
            hostname="https://xyz.node.k8s.prd.nos.ci",
            gpu_total=1,
            vcpu_total=4,
            ram_gb_total=16,
            node_class="gpu",
            metadata={},
            expose_url="https://xyz.node.k8s.prd.nos.ci",
        )
        assert result is True, "finalize_direct_node should return True on successful finalize"

        async with pool.acquire() as c:
            count = await c.fetchval(
                "SELECT COUNT(*) FROM compute_inventory WHERE pool_id=$1",
                pool_id,
            )
        assert count == 1, f"Expected exactly 1 node for the pool, got {count}"
    finally:
        await _cleanup_org_and_pool(pool, org_id, pool_id)


async def test_finalize_direct_node_with_tx(pool):
    """finalize_direct_node accepts an explicit transaction (tx= param)."""
    repo = InventoryRepository(pool)
    org_id, pool_id = await _seed_org_and_pool(pool, provider="akash", gpu_count=1)
    instance_id = f"akash-lease-abc123-{_run_id()}"
    try:
        node_id = await repo.create_placeholder(
            pool_id=pool_id, gpu_total=1, initial_alloc=1
        )

        async with pool.acquire() as conn:
            async with conn.transaction():
                result = await repo.finalize_direct_node(
                    node_id=node_id,
                    provider_instance_id=instance_id,
                    hostname="akash-node.example.com",
                    gpu_total=1,
                    vcpu_total=8,
                    ram_gb_total=32,
                    node_class="gpu",
                    metadata={"provider": "akash"},
                    expose_url="https://akash-node.example.com",
                    tx=conn,
                )
        assert result is True, "finalize_direct_node with tx should return True on success"

        row = await repo.get_node_by_id(node_id)
        assert row is not None
        assert row["state"] == "ready"
        assert row["provider_instance_id"] == instance_id
    finally:
        await _cleanup_org_and_pool(pool, org_id, pool_id)


async def test_finalize_direct_node_expose_url_none(pool):
    """expose_url=None is stored as NULL without error."""
    repo = InventoryRepository(pool)
    org_id, pool_id = await _seed_org_and_pool(pool, provider="nosana", gpu_count=1)
    instance_id = f"nosana-job-no-url-{_run_id()}"
    try:
        node_id = await repo.create_placeholder(
            pool_id=pool_id, gpu_total=1, initial_alloc=1
        )

        result = await repo.finalize_direct_node(
            node_id=node_id,
            provider_instance_id=instance_id,
            hostname="node.internal",
            gpu_total=1,
            vcpu_total=2,
            ram_gb_total=8,
            node_class="gpu",
            metadata={},
            expose_url=None,
        )
        assert result is True, "finalize_direct_node should return True on success"

        row = await repo.get_node_by_id(node_id)
        assert row is not None
        assert row["state"] == "ready"
        assert row.get("expose_url") is None
    finally:
        await _cleanup_org_and_pool(pool, org_id, pool_id)


async def test_finalize_direct_node_metadata_empty_dict(pool):
    """An empty metadata dict is stored as '{}' jsonb without error."""
    repo = InventoryRepository(pool)
    org_id, pool_id = await _seed_org_and_pool(pool, provider="nosana", gpu_count=1)
    instance_id = f"nosana-job-empty-meta-{_run_id()}"
    try:
        node_id = await repo.create_placeholder(
            pool_id=pool_id, gpu_total=1, initial_alloc=1
        )

        result = await repo.finalize_direct_node(
            node_id=node_id,
            provider_instance_id=instance_id,
            hostname="node.internal",
            gpu_total=1,
            vcpu_total=2,
            ram_gb_total=8,
            node_class="gpu",
            metadata={},
            expose_url=None,
        )
        assert result is True, "finalize_direct_node should return True on success"

        row = await repo.get_node_by_id(node_id)
        assert row is not None
        meta = row.get("metadata") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert meta == {}
    finally:
        await _cleanup_org_and_pool(pool, org_id, pool_id)


async def test_finalize_direct_node_unknown_or_finalized_is_noop(pool):
    """finalize_direct_node returns False and changes nothing on a non-existent or
    already-finalized node.

    Three sub-cases:
    1. Random UUID that was never inserted → returns False.
    2. First finalize on a fresh placeholder → returns True.
    3. Second finalize on the same (now 'ready') placeholder → returns False,
       and the row fields from the first call are NOT overwritten.
    """
    repo = InventoryRepository(pool)
    org_id, pool_id = await _seed_org_and_pool(pool, provider="nosana", gpu_count=1)
    instance_id_first = f"nosana-job-first-{_run_id()}"
    instance_id_second = f"nosana-job-second-{_run_id()}"
    try:
        # Sub-case 1: completely unknown node_id returns False.
        ghost_id = uuid4()
        result = await repo.finalize_direct_node(
            node_id=ghost_id,
            provider_instance_id=instance_id_first,
            hostname="ghost.node",
            gpu_total=1,
            vcpu_total=4,
            ram_gb_total=16,
            node_class="gpu",
            metadata={"ghost": True},
            expose_url=None,
        )
        assert result is False, (
            f"finalize on a non-existent node_id should return False, got {result!r}"
        )

        # Sub-case 2: first finalize on a real placeholder → True.
        node_id = await repo.create_placeholder(
            pool_id=pool_id, gpu_total=1, initial_alloc=1
        )
        result_first = await repo.finalize_direct_node(
            node_id=node_id,
            provider_instance_id=instance_id_first,
            hostname="first-hostname.internal",
            gpu_total=1,
            vcpu_total=4,
            ram_gb_total=16,
            node_class="gpu",
            metadata={"call": "first"},
            expose_url="https://first.example.com",
        )
        assert result_first is True, (
            f"first finalize_direct_node should return True, got {result_first!r}"
        )

        # Sub-case 3: second finalize on the same (now 'ready') row → False, no overwrite.
        result_second = await repo.finalize_direct_node(
            node_id=node_id,
            provider_instance_id=instance_id_second,
            hostname="second-hostname.internal",
            gpu_total=99,
            vcpu_total=99,
            ram_gb_total=999,
            node_class="cpu",
            metadata={"call": "second"},
            expose_url="https://second.example.com",
        )
        assert result_second is False, (
            f"second finalize_direct_node (row already 'ready') should return False, "
            f"got {result_second!r}"
        )

        # Verify first call's values are intact (not overwritten by the second call).
        row = await repo.get_node_by_id(node_id)
        assert row is not None
        assert row["state"] == "ready", f"state should still be ready, got {row['state']!r}"
        assert row["provider_instance_id"] == instance_id_first, (
            f"provider_instance_id should be from first call, got {row['provider_instance_id']!r}"
        )
        assert row["hostname"] == "first-hostname.internal", (
            f"hostname should be from first call, got {row['hostname']!r}"
        )
        assert row["vcpu_total"] == 4, (
            f"vcpu_total should be from first call (4), got {row['vcpu_total']!r}"
        )
        assert row.get("expose_url") == "https://first.example.com", (
            f"expose_url should be from first call, got {row.get('expose_url')!r}"
        )
        meta = row.get("metadata") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert meta.get("call") == "first", (
            f"metadata should be from first call, got {meta!r}"
        )
    finally:
        await _cleanup_org_and_pool(pool, org_id, pool_id)
