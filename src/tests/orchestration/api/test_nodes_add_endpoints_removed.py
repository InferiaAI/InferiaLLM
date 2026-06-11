"""Confirm POST /v1/nodes/add/* endpoints are removed (T11).

Nodes are now created at /deploy time via PoolPlacer (T7); the public
add-node REST surface has been retired.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from services.orchestration.api import nodes as nodes_api

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("provider_path", [
    "/v1/nodes/add/worker",
    "/v1/nodes/add/aws",
    "/v1/nodes/add/nosana",
    "/v1/nodes/add/akash",
])
async def test_add_provider_endpoints_return_404(provider_path):
    app = FastAPI()
    app.include_router(nodes_api.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(provider_path, json={"node_name": "test"})
    assert r.status_code == 404, (
        f"Expected 404 for {provider_path}, got {r.status_code}: {r.text}"
    )
