"""GET /deployment/logs/{id}/stream must resolve a node during DEPLOYING.

`model_deployments.node_ids` is only populated at RUNNING; during DEPLOYING only
`target_node_id` is set. The stream endpoint must fall back to target_node_id so
the dashboard can stream image-pull / lifecycle logs while the model container is
still being pulled. It should only error when BOTH are null.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from orchestration.model_deployment import (
    deployment_server,
)

pytestmark = pytest.mark.asyncio


def _app():
    app = FastAPI()
    app.include_router(deployment_server.router)
    return app


class _SeqConn:
    """Returns the scripted fetchrow rows in order, then None."""

    def __init__(self, rows):
        self._rows = list(rows)
        self._i = 0

    async def fetchrow(self, *args, **kwargs):
        row = self._rows[self._i] if self._i < len(self._rows) else None
        self._i += 1
        return row

    async def close(self):
        pass


async def _get_stream(dep_id, rows):
    transport = ASGITransport(app=_app())
    with patch.object(
        deployment_server.asyncpg, "connect", AsyncMock(return_value=_SeqConn(rows))
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.get(f"/deployment/logs/{dep_id}/stream")


async def test_stream_falls_back_to_target_node_id_during_deploying():
    dep_id = str(uuid4())
    target_node = str(uuid4())
    dep_row = {
        "provider": "aws",
        "provider_credential_name": None,
        "node_ids": None,  # not set until RUNNING
        "target_node_id": target_node,
        "org_id": "org-1",
    }
    node_row = {"provider_instance_id": "i-123", "agent_kind": "worker"}

    r = await _get_stream(dep_id, [dep_row, node_row])
    assert r.status_code == 200, r.text
    body = r.json()
    assert "error" not in body, body
    assert target_node in body["ws_url"]
    assert f"deployment={dep_id}" in body["ws_url"]


async def test_stream_errors_only_when_no_node_at_all():
    dep_id = str(uuid4())
    dep_row = {
        "provider": "aws",
        "provider_credential_name": None,
        "node_ids": None,
        "target_node_id": None,  # nothing bound yet
        "org_id": "org-1",
    }

    r = await _get_stream(dep_id, [dep_row])
    assert r.status_code == 200, r.text
    assert r.json() == {"error": "No nodes assigned to this deployment yet."}
