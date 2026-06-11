"""Unit tests for DELETE /deployment/delete/{id}.

The route connects via module-level POSTGRES_DSN, so we patch asyncpg.connect
with a fake conn and assert the state-gating logic:
- terminal states (STOPPED/TERMINATED/FAILED) and the pre-binding CREATED/PENDING
  states (no node bound) are deletable;
- resource-holding states (PENDING_NODE/DEPLOYING/RUNNING) are rejected with 400.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from orchestration.models.model_deployment import (
    deployment_server,
)

pytestmark = pytest.mark.asyncio


class _FakeConn:
    def __init__(self, *, row):
        self._row = row
        self.executed: list[str] = []

    async def fetchrow(self, sql, *args):
        return self._row

    async def execute(self, sql, *args):
        self.executed.append(" ".join(sql.split()))
        return "DELETE 1"

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


async def test_delete_404_when_missing():
    conn = _FakeConn(row=None)
    p1, p2, p3 = _patches(conn)
    with p1, p2, p3:
        r = await _delete(f"/deployment/delete/{uuid4()}")
    assert r.status_code == 404


@pytest.mark.parametrize("state", ["STOPPED", "TERMINATED", "FAILED"])
async def test_delete_allows_terminal_states(state):
    conn = _FakeConn(row={"state": state, "target_node_id": None})
    p1, p2, p3 = _patches(conn)
    with p1, p2, p3:
        r = await _delete(f"/deployment/delete/{uuid4()}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "DELETED"
    assert any("DELETE FROM model_deployments" in s for s in conn.executed)


@pytest.mark.parametrize("state", ["CREATED", "PENDING"])
async def test_delete_allows_prebinding_states_without_node(state):
    """CREATED/PENDING with no node bound have no GPU/worker resources — deletable."""
    conn = _FakeConn(row={"state": state, "target_node_id": None})
    p1, p2, p3 = _patches(conn)
    with p1, p2, p3:
        r = await _delete(f"/deployment/delete/{uuid4()}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "DELETED"


@pytest.mark.parametrize("state", ["PENDING_NODE", "DEPLOYING", "RUNNING"])
async def test_delete_rejects_resource_holding_states(state):
    conn = _FakeConn(row={"state": state, "target_node_id": uuid4()})
    p1, p2, p3 = _patches(conn)
    with p1, p2, p3:
        r = await _delete(f"/deployment/delete/{uuid4()}")
    assert r.status_code == 400
    assert "Stop it first" in r.json()["detail"]
    # Must NOT have deleted anything.
    assert not any("DELETE FROM model_deployments" in s for s in conn.executed)


async def test_delete_rejects_created_when_node_bound():
    """Defensive: CREATED but somehow node-bound is NOT deletable directly."""
    conn = _FakeConn(row={"state": "CREATED", "target_node_id": uuid4()})
    p1, p2, p3 = _patches(conn)
    with p1, p2, p3:
        r = await _delete(f"/deployment/delete/{uuid4()}")
    assert r.status_code == 400
