"""Tests for DeploymentLinker hook in register_worker endpoint.

Verifies that:
  1. RegisterRequest requires pool_id (Pydantic validates this).
  2. After upsert_worker, on_worker_ready is invoked exactly once.
"""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from services.orchestration.api import workers as workers_api

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def app_and_deps():
    """Set up a FastAPI app with workers_api router and mocked dependencies."""
    auth = MagicMock()
    auth.verify_bootstrap_token = MagicMock(
        return_value=type("C", (), {"pool_id": str(uuid4())})()
    )
    auth.mint_worker_token = MagicMock(return_value="jwt-token-mock")

    registry = MagicMock()

    pool_id = uuid4()
    node_id = uuid4()
    node_row = {
        "id": node_id,
        "pool_id": pool_id,
        "node_name": f"wkr-{uuid4().hex[:8]}",
        "advertise_url": "http://127.0.0.1:8080",
        "allocatable": {},
        "labels": None,
        "kind": "worker",
    }
    inventory = MagicMock()
    inventory.upsert_worker = AsyncMock(return_value=node_row)

    app = FastAPI()
    app.state.pool = MagicMock()
    app.state.worker_controller = AsyncMock()
    app.state.event_bus = None

    workers_api.configure(auth=auth, registry=registry, inventory=inventory)
    app.include_router(workers_api.router)

    yield app, auth, inventory, pool_id, node_id


async def test_register_worker_with_missing_pool_id_returns_422(app_and_deps):
    """RegisterRequest declares pool_id as required — Pydantic auto-422s."""
    app, _, _, _, _ = app_and_deps
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/workers/register", json={
            "node_name": "wkr-1",
            "advertise_url": "http://127.0.0.1:8080",
            # pool_id intentionally omitted
        })
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"


async def test_register_worker_does_NOT_fire_linker_hook(app_and_deps):
    """register_worker must NOT fire on_worker_ready: it returns the
    worker_jwt the worker only THEN uses to open the WS control channel, so
    load_model at register time always races the channel and fails
    NodeUnreachableError (marking the deploy FAILED). The linker is fired
    from worker_channel instead. This guards against re-introducing the
    premature trigger."""
    app, _, inventory, pool_id, node_id = app_and_deps

    with patch(
        "services.orchestration.api.workers._consume_bootstrap_token",
        new_callable=AsyncMock,
    ) as mock_consume, patch(
        "services.orchestration.model_deployment."
        "deployment_linker.DeploymentLinker.on_worker_ready",
        new_callable=AsyncMock,
    ) as mock_ready:
        mock_consume.return_value = type(
            "Claim", (), {"pool_id": str(pool_id)},
        )()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/v1/workers/register", json={
                "bootstrap_token": "bt-test-token-long-enough",
                "node_name": f"wkr-{uuid4().hex[:8]}",
                "pool_id": str(pool_id),
                "advertise_url": "http://127.0.0.1:8080",
            })

    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["node_id"] == str(node_id)
    assert body["worker_jwt"] == "jwt-token-mock"
    # The linker is deferred to the channel — register must not touch it.
    mock_ready.assert_not_awaited()


async def test_channel_ready_helper_fires_linker_with_node_uuid():
    """_fire_linker_on_channel_ready schedules on_worker_ready(node_uuid) as
    a background task — this is the path that actually loads the model once
    the worker's control channel is connected."""
    node_id = uuid4()
    ws = MagicMock()
    ws.app.state.pool = MagicMock()
    ws.app.state.worker_controller = AsyncMock()
    ws.app.state.event_bus = None

    with patch(
        "services.orchestration.model_deployment."
        "deployment_linker.DeploymentLinker.on_worker_ready",
        new_callable=AsyncMock,
    ) as mock_ready:
        workers_api._fire_linker_on_channel_ready(ws, str(node_id))
        # Let the scheduled task run.
        import asyncio
        for _ in range(20):
            await asyncio.sleep(0)
            if mock_ready.await_count:
                break

    mock_ready.assert_awaited_once()
    assert mock_ready.await_args.args[0] == node_id  # passed as a UUID
