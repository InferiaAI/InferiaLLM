"""Smoke-tests the 20260528_provisioning_jobs migration against a real PG.

Run with INFERIA_TEST_DATABASE_URL pointing at an empty test DB that has
the existing global_schema.sql + all prior migrations applied. Skipped
if that env var is not set.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import asyncpg
import pytest

MIGRATION = Path(__file__).resolve().parents[6] / "infra" / "schema" / "migrations" / "20260528_provisioning_jobs.sql"


@pytest.fixture
def test_database_url() -> str:
    url = os.environ.get("INFERIA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("INFERIA_TEST_DATABASE_URL not set")
    return url


@pytest.mark.asyncio
async def test_migration_applies_cleanly(test_database_url):
    """Running the migration on a fresh DB produces the expected schema."""
    sql = MIGRATION.read_text()
    conn = await asyncpg.connect(test_database_url)
    try:
        # Migrator runs statements in autocommit, so split on ';' boundaries
        # that terminate full statements (naive but adequate for our SQL).
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            await conn.execute(stmt)

        # 'failed' is now a node_state enum value.
        rows = await conn.fetch(
            "SELECT unnest(enum_range(NULL::node_state))::text AS v"
        )
        assert "failed" in [r["v"] for r in rows]

        # compute_inventory has instance_class + instance_type columns.
        cols = await conn.fetch(
            """SELECT column_name FROM information_schema.columns
               WHERE table_name='compute_inventory'
                 AND column_name IN ('instance_class','instance_type')"""
        )
        assert {r["column_name"] for r in cols} == {"instance_class", "instance_type"}

        # provisioning_jobs table exists with the expected columns.
        cols = await conn.fetch(
            """SELECT column_name FROM information_schema.columns
               WHERE table_name='provisioning_jobs'"""
        )
        names = {r["column_name"] for r in cols}
        expected = {
            "id", "node_id", "pool_id", "org_id", "provider", "spec",
            "phase", "attempt_count", "next_attempt_after",
            "last_error_code", "last_error_message", "last_error_hint",
            "error_class", "lease_holder", "lease_expires_at",
            "pulumi_stack_outputs", "created_at", "updated_at",
        }
        assert expected <= names, f"missing columns: {expected - names}"

        # Claimable index exists.
        idx = await conn.fetchval(
            """SELECT 1 FROM pg_indexes
               WHERE tablename='provisioning_jobs'
                 AND indexname='provisioning_jobs_claimable_idx'"""
        )
        assert idx == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_migration_is_idempotent(test_database_url):
    """Re-running the migration on an already-migrated DB is a no-op."""
    sql = MIGRATION.read_text()
    conn = await asyncpg.connect(test_database_url)
    try:
        for _ in range(2):
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                await conn.execute(stmt)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_migration_marks_inflight_inventory_as_failed(test_database_url):
    """An existing compute_inventory row in 'provisioning' becomes 'failed'
    with a matching provisioning_jobs row carrying UPGRADE_ABANDONED."""
    conn = await asyncpg.connect(test_database_url)
    try:
        pool_id = uuid.uuid4()
        node_id = uuid.uuid4()
        # Set up a pool + inventory row in 'provisioning'.
        await conn.execute(
            """INSERT INTO compute_pools (
                 id, pool_name, owner_type, provider, scheduling_policy,
                 lifecycle_state, org_id
               )
               VALUES (
                 $1, 'p-test-migration', 'organization', 'aws', '{}'::jsonb,
                 'running', 'org-test'
               )""",
            pool_id,
        )
        await conn.execute(
            """INSERT INTO compute_inventory (id, pool_id, provider,
                 provider_instance_id, state, agent_kind)
               VALUES ($1, $2, 'aws', 'placeholder:test', 'provisioning', 'worker')""",
            node_id, pool_id,
        )
        # Apply the migration.
        sql = MIGRATION.read_text()
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            await conn.execute(stmt)
        # Assert: inventory row now 'failed', a job row exists.
        state = await conn.fetchval(
            "SELECT state::text FROM compute_inventory WHERE id=$1", node_id
        )
        assert state == "failed"
        job = await conn.fetchrow(
            "SELECT phase, last_error_code FROM provisioning_jobs WHERE node_id=$1",
            node_id,
        )
        assert job["phase"] == "failed"
        assert job["last_error_code"] == "UPGRADE_ABANDONED"
    finally:
        # Cleanup
        await conn.execute("DELETE FROM provisioning_jobs WHERE node_id=$1", node_id)
        await conn.execute("DELETE FROM compute_inventory WHERE id=$1", node_id)
        await conn.execute("DELETE FROM compute_pools WHERE id=$1", pool_id)
        await conn.close()
