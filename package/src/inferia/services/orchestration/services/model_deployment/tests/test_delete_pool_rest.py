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
    def __init__(self, *, pool_row, active_count):
        self._pool_row = pool_row
        self._active = active_count
        self.executed: list[tuple] = []

    async def fetchrow(self, sql, *args):
        return self._pool_row

    async def fetchval(self, sql, *args):
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


async def test_delete_pool_202_cascade_terminates_active_deployments():
    """Active deployments no longer 409: the pool delete now cascade-terminates
    them (the nodes are being destroyed anyway) and returns 202."""
    pid = uuid4()
    conn = _FakeConn(pool_row={"id": pid, "is_active": True}, active_count=2)
    p1, p2, p3 = _patches(conn)
    with p1, p2, p3:
        r = await _delete(f"/deployment/pool/{pid}")
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "TERMINATING"
    sqls = [sql for sql, _ in conn.executed]
    # Cascade-terminates every non-terminal deployment in the pool.
    assert any(
        "model_deployments" in s and "state = 'TERMINATED'" in s
        and "pool_id = $1" in s
        and "state NOT IN ('STOPPED', 'TERMINATED', 'FAILED')" in s
        for s in sqls
    )


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
