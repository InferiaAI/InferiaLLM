"""GET /deployment/deployments must surface `inference_model` (the real model
slug) distinct from `model_name` (the human deployment name). Without it the
dashboard's Model column falls back to model_name and shows the deployment name
twice. Regression guard for that fix.

The route calls gRPC ListDeployments via _auth_channel and then asyncpg for
created_at enrichment; both are patched.
"""
from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from inferia.services.orchestration.services.model_deployment import (
    deployment_server,
)

pytestmark = pytest.mark.asyncio


def _app():
    app = FastAPI()
    app.include_router(deployment_server.router)
    return app


class _FakeConn:
    async def fetch(self, *args):
        return []

    async def close(self):
        pass


def _fake_deployment(**over):
    base = dict(
        deployment_id=str(uuid4()),
        model_name="My Prod Bot",
        inference_model="hf://gemma3:4b",
        model_version="1",
        state="RUNNING",
        replicas=1,
        pool_id=str(uuid4()),
        engine="ollama",
        endpoint="http://x:11434",
        org_id="org-1",
        error_message="",
    )
    base.update(over)
    return SimpleNamespace(**base)


@contextlib.asynccontextmanager
async def _fake_channel():
    yield MagicMock()


async def _get(path):
    transport = ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.get(path)


async def test_listing_includes_inference_model_distinct_from_model_name():
    dep = _fake_deployment()
    resp = SimpleNamespace(deployments=[dep])
    stub = MagicMock()
    stub.ListDeployments = AsyncMock(return_value=resp)

    with patch.object(deployment_server, "_auth_channel", _fake_channel), \
         patch.object(
             deployment_server.model_deployment_pb2_grpc,
             "ModelDeploymentServiceStub",
             return_value=stub,
         ), \
         patch.object(
             deployment_server.asyncpg, "connect",
             AsyncMock(return_value=_FakeConn()),
         ):
        r = await _get("/deployment/deployments?org_id=org-1")

    assert r.status_code == 200, r.text
    body = r.json()["deployments"]
    assert len(body) == 1
    row = body[0]
    assert row["inference_model"] == "hf://gemma3:4b"
    assert row["model_name"] == "My Prod Bot"
    # The two must be distinct fields so the UI can show the real model.
    assert row["inference_model"] != row["model_name"]
