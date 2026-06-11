"""Integration tests for DELETE /deployment/delete/{id} node teardown (Task 2.5).

The REST ``/delete`` route was a DB-only DROP: a terminal deploy (FAILED/STOPPED)
that still OWNED a live EC2 node leaked the instance because the row was dropped
with no Pulumi teardown. These tests assert that ``delete_deployment`` now routes
an owned node through the SAME force_cancel -> reconciler CancelHandler path that
/terminate's C9 branch uses, and that it is consistent about deleting
``deployment_terminal_logs`` rows.

The route connects via the module-level ``POSTGRES_DSN`` (a raw asyncpg
connection / a small pool for the teardown), so we run against the real test
database and patch ``ProvisioningJobRepository.force_cancel`` to assert it fires.

Run with:
    POSTGRES_DSN=postgresql://inferia:inferia@inferia-testpg:5432/inferia_test \\
    python -m pytest test_delete_endpoint_node_teardown.py -v
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport

from services.orchestration.model_deployment import (
    deployment_server,
)

# Reuse the real-PG fixtures + seed helpers from the terminate refcount suite.
from tests.orchestration.model_deployment.test_terminate_endpoint_refcount import (  # noqa: E501
    db_pool,  # noqa: F401  (pytest fixture)
    _seed_pool,
    _seed_ready_node,
    _seed_deploy,
)

pytestmark = pytest.mark.asyncio


# force_cancel is patched at the class so it fires regardless of which pool the
# route builds internally (the route connects via POSTGRES_DSN, not app.state).
_FORCE_CANCEL = (
    "services.orchestration.provisioning_state_machine.jobs.repository."
    "ProvisioningJobRepository.force_cancel"
)


@pytest_asyncio.fixture(autouse=True)
def _no_audit():
    """log_audit_event / _lookup_org_id are unrelated to teardown; stub them so
    the tests don't depend on the audit pipeline."""
    with patch.object(deployment_server, "log_audit_event", AsyncMock()), \
            patch.object(deployment_server, "_lookup_org_id",
                         AsyncMock(return_value=str(uuid4()))):
        yield


async def _delete(deploy_id):
    """DELETE /deployment/delete/{id} against an isolated app (the route's DB
    access goes through POSTGRES_DSN, so no app.state.pool is needed)."""
    app = FastAPI()
    app.include_router(deployment_server.router)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.delete(f"/deployment/delete/{deploy_id}")


async def _row_exists(pool, deploy_id) -> bool:
    async with pool.acquire() as c:
        return bool(await c.fetchval(
            "SELECT 1 FROM model_deployments WHERE deployment_id=$1", deploy_id,
        ))


async def test_delete_failed_with_owned_node_destroys_and_drops(db_pool):  # noqa: F811
    """FAILED deploy still owning a live node, no other live deploy =>
    force_cancel fires (node teardown) AND the row is dropped."""
    pool = db_pool
    pool_id = await _seed_pool(pool, provider="aws")
    node_id = await _seed_ready_node(pool, pool_id, gpu_total=4, gpu_allocated=0)
    deploy_id = await _seed_deploy(
        pool, pool_id, state="FAILED", gpu_per_replica=1, target_node_id=node_id,
    )

    with patch(_FORCE_CANCEL, new_callable=AsyncMock,
               return_value=True) as mock_force_cancel:
        resp = await _delete(deploy_id)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DELETED"

    # Node was torn down via force_cancel(node_id=...) — same mechanism as
    # /terminate's C9 path (NOT enqueue).
    mock_force_cancel.assert_awaited_once()
    assert mock_force_cancel.await_args.kwargs["node_id"] == node_id

    # metadata.terminating flagged by _initiate_node_destroy.
    async with pool.acquire() as c:
        terminating = await c.fetchval(
            "SELECT metadata->>'terminating' FROM compute_inventory WHERE id=$1",
            node_id,
        )
    assert terminating == "true"

    # Row dropped.
    assert not await _row_exists(pool, deploy_id)


async def test_delete_with_other_live_deploy_keeps_node_but_drops_row(db_pool):  # noqa: F811
    """FAILED deploy whose node still has ANOTHER live (RUNNING) deploy =>
    node NOT destroyed (no force_cancel) but the FAILED row is still dropped."""
    pool = db_pool
    pool_id = await _seed_pool(pool, provider="aws")
    node_id = await _seed_ready_node(pool, pool_id, gpu_total=4, gpu_allocated=1)
    org = str(uuid4())
    failed = await _seed_deploy(
        pool, pool_id, state="FAILED", gpu_per_replica=1,
        target_node_id=node_id, org_id=org,
    )
    other = await _seed_deploy(
        pool, pool_id, state="RUNNING", gpu_per_replica=1,
        target_node_id=node_id, org_id=org,
    )

    with patch(_FORCE_CANCEL, new_callable=AsyncMock,
               return_value=True) as mock_force_cancel:
        resp = await _delete(failed)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DELETED"

    # Another live deploy keeps the node — must NOT tear it down.
    mock_force_cancel.assert_not_awaited()

    # FAILED row dropped; the still-live deploy + node remain.
    assert not await _row_exists(pool, failed)
    assert await _row_exists(pool, other)
    async with pool.acquire() as c:
        assert await c.fetchval(
            "SELECT 1 FROM compute_inventory WHERE id=$1", node_id,
        )


async def test_delete_without_node_attempts_no_teardown(db_pool):  # noqa: F811
    """Regression: a terminal deploy with target_node_id=NULL deletes cleanly
    with no teardown attempted (the existing happy-path delete still works)."""
    pool = db_pool
    pool_id = await _seed_pool(pool, provider="aws")
    deploy_id = await _seed_deploy(
        pool, pool_id, state="FAILED", gpu_per_replica=1, target_node_id=None,
    )

    with patch(_FORCE_CANCEL, new_callable=AsyncMock,
               return_value=True) as mock_force_cancel:
        resp = await _delete(deploy_id)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DELETED"
    mock_force_cancel.assert_not_awaited()
    assert not await _row_exists(pool, deploy_id)


async def test_delete_removes_deployment_terminal_logs(db_pool):  # noqa: F811
    """The manual cleanup block must delete deployment_terminal_logs rows for
    the deploy (consistency with the inference_logs delete)."""
    pool = db_pool
    pool_id = await _seed_pool(pool, provider="aws")
    deploy_id = await _seed_deploy(
        pool, pool_id, state="FAILED", gpu_per_replica=1, target_node_id=None,
    )
    # Seed a terminal-log row for this deploy.
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO deployment_terminal_logs(deployment_id, log_lines, "
            "trigger_event) VALUES ($1, $2, $3)",
            deploy_id, ["boom", "stack trace"], "FAILED",
        )
        before = await c.fetchval(
            "SELECT count(*) FROM deployment_terminal_logs WHERE deployment_id=$1",
            deploy_id,
        )
    assert before == 1

    with patch(_FORCE_CANCEL, new_callable=AsyncMock, return_value=True):
        resp = await _delete(deploy_id)
    assert resp.status_code == 200, resp.text

    async with pool.acquire() as c:
        after = await c.fetchval(
            "SELECT count(*) FROM deployment_terminal_logs WHERE deployment_id=$1",
            deploy_id,
        )
    assert after == 0
    assert not await _row_exists(pool, deploy_id)
