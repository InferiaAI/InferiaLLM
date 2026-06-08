"""Unit tests for DELETE /deployment/pool/{id} — the route the dashboard's
'Delete Pool' button calls. Previously MISSING (the button 404'd); now it
tears down every node's EC2 via the reconciler and soft-deletes the pool.

The route connects via module-level POSTGRES_DSN, so we patch
asyncpg.connect with a fake conn and assert the branch + the teardown SQL.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from inferia.services.orchestration.services.model_deployment import (
    deployment_server,
)

pytestmark = pytest.mark.asyncio


class _FakeConn:
    def __init__(self, *, pool_row, active_count, live_inventory=0):
        self._pool_row = pool_row
        self._active = active_count
        self._live_inventory = live_inventory
        self.executed: list[tuple] = []

    async def fetchrow(self, sql, *args):
        return self._pool_row

    async def fetchval(self, sql, *args):
        # The empty-pool finalize check counts compute_inventory rows still
        # attached to the pool; everything else (duplicate-name guard etc.)
        # reuses ``active_count``.
        if "compute_inventory" in sql and "count" in sql.lower():
            return self._live_inventory
        return self._active

    async def execute(self, sql, *args):
        self.executed.append((" ".join(sql.split()), args))
        return "UPDATE 1"

    def transaction(self):
        class _Tx:
            async def __aenter__(self_):
                return self_
            async def __aexit__(self_, *a):
                return False
        return _Tx()

    async def close(self):
        pass


def _app():
    app = FastAPI()
    app.include_router(deployment_server.router)
    return app


def _patches(conn):
    return (
        patch.object(deployment_server.asyncpg, "connect",
                     AsyncMock(return_value=conn)),
        patch.object(deployment_server, "log_audit_event", AsyncMock()),
        patch.object(deployment_server, "_lookup_org_id",
                     AsyncMock(return_value=None)),
    )


async def _delete(path):
    transport = ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.delete(path)


async def test_delete_pool_404_when_missing():
    conn = _FakeConn(pool_row=None, active_count=0)
    p1, p2, p3 = _patches(conn)
    with p1, p2, p3:
        r = await _delete(f"/deployment/pool/{uuid4()}")
    assert r.status_code == 404


async def test_delete_pool_202_cascade_deletes_deployments():
    """Active deployments no longer 409: the pool delete now cascade-DELETES
    every deployment in the pool (and detaches/removes their dependents) and
    returns 202."""
    pid = uuid4()
    conn = _FakeConn(pool_row={"id": pid, "is_active": True}, active_count=2)
    p1, p2, p3 = _patches(conn)
    with p1, p2, p3:
        r = await _delete(f"/deployment/pool/{pid}")
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "TERMINATING"
    sqls = [sql for sql, _ in conn.executed]
    # Hard-deletes every deployment in the pool.
    assert any(
        "DELETE FROM model_deployments" in s and "pool_id = $1" in s
        for s in sqls
    )
    # Detaches dependents that lack ON DELETE behavior before the delete.
    assert any("UPDATE policies SET deployment_id = NULL" in s for s in sqls)
    assert any("UPDATE api_keys SET deployment_id = NULL" in s for s in sqls)
    assert any("DELETE FROM inference_logs" in s for s in sqls)
    # Must NOT leave them as TERMINATED rows.
    assert not any("state = 'TERMINATED'" in s for s in sqls)


async def test_delete_pool_202_force_cancels_jobs_and_soft_deletes():
    pid = uuid4()
    conn = _FakeConn(pool_row={"id": pid, "is_active": True}, active_count=0)
    p1, p2, p3 = _patches(conn)
    with p1, p2, p3:
        r = await _delete(f"/deployment/pool/{pid}")
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "TERMINATING"
    sqls = [sql for sql, _ in conn.executed]
    # 1) flips the pool's live jobs to cancelling (reconciler destroys EC2s)
    assert any(
        "provisioning_jobs" in s and "phase = 'cancelling'" in s
        and "pool_id = $1" in s and "phase NOT IN ('cancelling', 'terminated')" in s
        for s in sqls
    )
    # 2) marks nodes terminating for the dashboard
    assert any("compute_inventory" in s and "terminating" in s for s in sqls)
    # 3) soft-deletes the pool row
    assert any(
        "compute_pools" in s and "is_active = FALSE" in s for s in sqls
    )


async def test_delete_pool_400_on_bad_uuid():
    conn = _FakeConn(pool_row=None, active_count=0)
    p1, p2, p3 = _patches(conn)
    with p1, p2, p3:
        r = await _delete("/deployment/pool/not-a-uuid")
    assert r.status_code == 400


async def test_delete_empty_pool_finalizes_immediately():
    """An empty / already-drained pool (zero live compute_inventory rows) has
    NO node teardown event coming — the reconciler finalizer would never fire,
    so the delete path itself must finalize: HARD-delete the compute_pools row
    in the same request instead of leaving it stuck 'terminating' forever."""
    pid = uuid4()
    conn = _FakeConn(
        pool_row={"id": pid, "is_active": True},
        active_count=0,
        live_inventory=0,  # zero nodes → finalize-when-empty must fire
    )
    p1, p2, p3 = _patches(conn)
    with p1, p2, p3:
        r = await _delete(f"/deployment/pool/{pid}")
    assert r.status_code == 202, r.text
    sqls = [sql for sql, _ in conn.executed]
    # The finalizer HARD-deletes the compute_pools row (frees the unique name).
    assert any(
        "DELETE FROM compute_pools WHERE id = $1" in s for s in sqls
    ), sqls
    # ...and detaches any divergent target_pool_id before that delete.
    assert any(
        "UPDATE model_deployments SET target_pool_id = NULL" in s for s in sqls
    ), sqls
    # ...and removes pool-scoped residue (events + bootstrap tokens).
    assert any(
        "DELETE FROM node_provisioning_events WHERE pool_id = $1" in s
        for s in sqls
    ), sqls
    assert any(
        "DELETE FROM worker_bootstrap_tokens WHERE pool_id = $1" in s
        for s in sqls
    ), sqls


async def test_delete_pool_with_live_nodes_defers_finalize():
    """A pool with live nodes still tearing down must NOT be hard-deleted at
    delete time — the per-node ``_teardown_node`` finalizes once the LAST node
    is purged. Only the soft-delete (terminating) lands now."""
    pid = uuid4()
    conn = _FakeConn(
        pool_row={"id": pid, "is_active": True},
        active_count=0,
        live_inventory=2,  # two nodes still tearing down
    )
    p1, p2, p3 = _patches(conn)
    with p1, p2, p3:
        r = await _delete(f"/deployment/pool/{pid}")
    assert r.status_code == 202, r.text
    sqls = [sql for sql, _ in conn.executed]
    # Soft-delete to terminating happened...
    assert any(
        "compute_pools" in s and "is_active = FALSE" in s for s in sqls
    )
    # ...but the HARD finalize delete did NOT (nodes still alive).
    assert not any(
        "DELETE FROM compute_pools WHERE id = $1" in s for s in sqls
    ), sqls
