"""Integration test: upgrade-day migration backfills in-flight inventory.

What this proves end-to-end:

1. A ``compute_inventory`` row left in state='provisioning' by an older
   inferia-app release (i.e. an in-flight pulumi up that the old
   fire-and-forget adapter never finished) gets caught by the upgrade
   migration's backfill clause.
2. The backfill inserts a ``provisioning_jobs`` row with phase='failed'
   + last_error_code='UPGRADE_ABANDONED' + error_class='PERMANENT' + a
   hint pointing the operator at the delete + recreate workflow.
3. The inventory row transitions to state='failed' (the 'failed' enum
   value lands in the sibling 20260528a migration -- that's why the
   migrations are split, see the WARNING in 20260528b_provisioning_jobs.sql).
4. GET /v1/nodes/{id}/provisioning then exposes that failure via the
   same response shape the dashboard's InstanceDetail consumes: a
   ``current_phase='failed'`` with ``terminal=True`` and an
   ``error.code='UPGRADE_ABANDONED'`` block whose hint mentions
   "delete" -- the dashboard surfaces that hint verbatim in the
   error banner so operators know what to do.

Why re-apply the migration instead of starting from a fresh DB: the
``app_with_real_db`` fixture already ran both migration files on entry
(no inflight rows existed, so the backfill clause was a no-op). To
exercise the backfill we INSERT an inflight inventory row first, then
re-run the migrations -- both files are documented as idempotent and
the second pass's INSERT...SELECT picks up our inflight row.

Gated on INFERIA_TEST_DATABASE_URL via the shared conftest.py fixture
(skipped cleanly when the env var is unset).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

# Migration files mirror conftest.MIGRATIONS exactly. We re-import the
# paths here (rather than the constant) so a change to the conftest list
# is caught by this test failing rather than silently agreeing.
_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[6]
    / "infra" / "schema" / "migrations"
)
MIGRATIONS = [
    _MIGRATIONS_DIR / "20260528a_node_state_failed.sql",
    _MIGRATIONS_DIR / "20260528b_provisioning_jobs.sql",
]


@pytest.mark.asyncio
async def test_migration_marks_inflight_inventory_and_exposes_via_http(
    app_with_real_db,
):
    """Apply the migration on top of a DB that has an in-flight
    provisioning row, then verify the HTTP response shape exposes the
    upgrade-abandoned failure."""
    app, client, pool = app_with_real_db

    pool_id = uuid.uuid4()
    node_id = uuid.uuid4()
    async with pool.acquire() as conn:
        # compute_pools has multiple NOT NULL columns -- mirror the
        # full insert shape used by test_migration.py rather than the
        # short form sketched in the plan (which would fail with
        # NOT NULL violations on pool_name / owner_type /
        # scheduling_policy).
        await conn.execute(
            """INSERT INTO compute_pools (
                 id, pool_name, owner_type, provider, scheduling_policy,
                 lifecycle_state, org_id
               )
               VALUES (
                 $1, 'p-test-upgrade', 'organization', 'aws',
                 '{}'::jsonb, 'running', 'org-upgrade'
               )
               ON CONFLICT (id) DO NOTHING""",
            pool_id,
        )
        await conn.execute(
            """INSERT INTO compute_inventory (id, pool_id, provider,
                   provider_instance_id, state, agent_kind)
               VALUES ($1, $2, 'aws', 'placeholder:upgrade-test',
                       'provisioning', 'worker')""",
            node_id, pool_id,
        )

    # Re-apply the migration. The first pass (in the fixture) was a
    # no-op for the backfill clause because no inflight rows existed.
    # This second pass picks up the row we just inserted via the
    # INSERT...SELECT in 20260528b's backfill section, plus the
    # UPDATE compute_inventory SET state='failed' right after it.
    # Splitting on ';' matches cli_init.py's production behavior.
    async with pool.acquire() as conn:
        for path in MIGRATIONS:
            sql = path.read_text()
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                await conn.execute(stmt)

    # HTTP shape exposes the upgrade-abandoned failure. This is the
    # exact response shape the dashboard's InstanceDetail Overview tab
    # consumes when it renders the error banner + Retry button.
    body = (await client.get(
        f"/v1/nodes/{node_id}/provisioning",
        headers={"Authorization": "Bearer test"},
    )).json()
    assert body["current_phase"] == "failed", body
    assert body["terminal"] is True, body
    assert body["error"] is not None, body
    assert body["error"]["code"] == "UPGRADE_ABANDONED", body
    assert body["error"]["class"] == "PERMANENT", body
    # The hint string starts with "Delete and recreate from the wizard."
    # -- substring-checking case-insensitively keeps the test resilient
    # to minor wording tweaks while still proving the operator-facing
    # delete instruction is present.
    assert "delete" in body["error"]["hint"].lower(), body

    # Cleanup. Order matters: provisioning_jobs has FK ON DELETE CASCADE
    # on node_id, so deleting compute_inventory cascades, but we drop
    # provisioning_jobs first for clarity. compute_pools last because
    # compute_inventory.pool_id references it.
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM provisioning_jobs WHERE node_id=$1", node_id,
        )
        await conn.execute(
            "DELETE FROM compute_inventory WHERE id=$1", node_id,
        )
        await conn.execute(
            "DELETE FROM compute_pools WHERE id=$1", pool_id,
        )
