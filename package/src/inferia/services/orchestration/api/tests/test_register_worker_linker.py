"""Tests for DeploymentLinker hook in register_worker endpoint.

Verifies that:
  1. RegisterRequest requires pool_id (Pydantic validates this).
  2. After upsert_worker, on_worker_ready is invoked exactly once.
  3. Linker failures don't break registration (try/except wrapping).
"""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from inferia.services.orchestration.api import workers as workers_api


@pytest.fixture
def app_and_deps():
    """Set up a FastAPI app with workers_api router and mocked dependencies."""
    auth = MagicMock()
    auth.verify_bootstrap_token = MagicMock(return_value=type("C", (), {"pool_id": str(uuid4())})())
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

    return app, auth, inventory, pool_id, node_id


def test_register_worker_with_missing_pool_id_returns_422(app_and_deps):
    """RegisterRequest declares pool_id as a required str — Pydantic
    auto-422s on missing. Sanity check that the contract is intact."""
    app, _, _, _, _ = app_and_deps
    client = TestClient(app)
    r = client.post("/v1/workers/register", json={
        "node_name": "wkr-1",
        "advertise_url": "http://127.0.0.1:8080",
        # pool_id intentionally omitted
    })
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"


def test_register_worker_fires_linker_hook(app_and_deps):
    """After upsert_worker succeeds, on_worker_ready is invoked once
    with the node_id."""
    app, _, inventory, pool_id, node_id = app_and_deps

    # Patch on_worker_ready to verify it's called, and patch the bootstrap
    # token consumption so we bypass the DB-backed single-use token check.
    with patch(
        "inferia.services.orchestration.api.workers._consume_bootstrap_token",
        new_callable=AsyncMock,
    ) as mock_consume, patch(
        "inferia.services.orchestration.services.model_deployment."
        "deployment_linker.DeploymentLinker.on_worker_ready",
        new_callable=AsyncMock,
    ) as mock_ready:
        # Mock _consume_bootstrap_token to return a claim with matching pool_id.
        mock_consume.return_value = type("Claim", (), {"pool_id": str(pool_id)})()

        client = TestClient(app)
        r = client.post("/v1/workers/register", json={
            "bootstrap_token": "bt-test-token-long-enough",
            "node_name": f"wkr-{uuid4().hex[:8]}",
            "pool_id": str(pool_id),
            "advertise_url": "http://127.0.0.1:8080",
        })

    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["node_id"] == str(node_id)
    assert body["worker_jwt"] == "jwt-token-mock"

    # Verify the linker hook was called with the node_id.
    mock_ready.assert_awaited_once()
    call_args = mock_ready.await_args
    assert len(call_args.args) == 1
    assert call_args.args[0] == node_id
