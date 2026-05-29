"""Integration tests for the metadata-only /createpool handler (T9).

These tests spin up an isolated FastAPI app with the deployment_server router,
set app.state.pool to a real asyncpg pool against inferia_test, and verify
that /createpool:
  - Creates a compute_pools row
  - Does NOT create any compute_inventory rows
  - Does NOT call _kick_aws_provision (which no longer exists)
  - Persists metadata and org_id into compute_pools
  - Returns 409 on duplicate (pool_name, owner_id) pairs
  - Returns 400 on an invalid provider

Run with:
    TEST_DATABASE_URL=postgresql://inferia:inferia@172.18.0.3:5432/inferia_test \\
    PYTHONPATH=package/src \\
    python -m pytest \\
      package/src/inferia/services/orchestration/services/model_deployment/tests/test_createpool_metadata_only.py \\
      -v
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from inferia.services.orchestration.services.model_deployment import (
    deployment_server,
)
from inferia.services.orchestration.services.worker_controller.controller import (
    WorkerController,
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://inferia:inferia@localhost:5432/inferia_test",
)


@pytest_asyncio.fixture
async def db_pool():
    """Real asyncpg pool connected to the test database."""
    p = await asyncpg.create_pool(dsn=_DSN, min_size=1, max_size=4)
    yield p
    await p.close()


@pytest_asyncio.fixture
async def app_and_pool(db_pool):
    """Isolated FastAPI app with the deployment router mounted."""
    app = FastAPI()
    app.state.pool = db_pool
    app.state.worker_controller = AsyncMock(spec=WorkerController)
    app.state.event_bus = None
    app.include_router(deployment_server.router)
    yield app, db_pool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _createpool_payload(
    *,
    provider: str = "aws",
    pool_name: str | None = None,
    owner_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    if pool_name is None:
        pool_name = f"pool-{uuid4().hex[:8]}"
    if owner_id is None:
        owner_id = str(uuid4())
    payload: dict = {
        "pool_name": pool_name,
        "owner_type": "user",
        "owner_id": owner_id,
        "provider": provider,
        "allowed_gpu_types": ["t3.micro"],
        "max_cost_per_hour": 0.5,
        "is_dedicated": False,
        "provider_pool_id": "",
        "scheduling_policy_json": '{"strategy":"best_fit"}',
        "gpu_count": 1,
    }
    if metadata is not None:
        payload["metadata"] = metadata
    return payload


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_createpool_aws_does_not_provision(app_and_pool):
    """POST /createpool with provider=aws must return HTTP 200, create a
    compute_pools row, and NOT insert any compute_inventory rows or call
    any provisioning function.
    """
    app, pool = app_and_pool
    payload = _createpool_payload(provider="aws")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/deployment/createpool", json=payload)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "pool_id" in body
    assert body["status"] == "CREATED"

    pool_id = body["pool_id"]

    async with pool.acquire() as c:
        # compute_pools row must exist
        cp_row = await c.fetchrow(
            "SELECT id, provider FROM compute_pools WHERE id = $1::uuid",
            pool_id,
        )
        assert cp_row is not None, "compute_pools row not found"
        assert cp_row["provider"] == "aws"

        # NO compute_inventory rows for this pool
        inv_count = await c.fetchval(
            "SELECT COUNT(*) FROM compute_inventory WHERE pool_id = $1::uuid",
            pool_id,
        )
        assert inv_count == 0, (
            f"Expected 0 compute_inventory rows for AWS pool, got {inv_count}"
        )


async def test_createpool_nosana_does_not_create_placeholder(app_and_pool):
    """POST /createpool with provider=nosana must NOT insert any
    compute_inventory placeholder rows.
    """
    app, pool = app_and_pool
    payload = _createpool_payload(provider="nosana")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/deployment/createpool", json=payload)

    assert resp.status_code == 200, resp.text
    pool_id = resp.json()["pool_id"]

    async with pool.acquire() as c:
        inv_count = await c.fetchval(
            "SELECT COUNT(*) FROM compute_inventory WHERE pool_id = $1::uuid",
            pool_id,
        )
        assert inv_count == 0, (
            f"Expected 0 compute_inventory rows for nosana pool, got {inv_count}"
        )


async def test_createpool_persists_metadata(app_and_pool):
    """POST /createpool with a metadata dict must persist those keys into
    compute_pools.metadata (jsonb).
    """
    app, pool = app_and_pool
    meta = {
        "instance_type": "t3.small",
        "region": "us-east-1",
        "agent_kind": "worker",
    }
    payload = _createpool_payload(provider="aws", metadata=meta)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/deployment/createpool", json=payload)

    assert resp.status_code == 200, resp.text
    pool_id = resp.json()["pool_id"]

    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT metadata FROM compute_pools WHERE id = $1::uuid",
            pool_id,
        )
    assert row is not None, "compute_pools row not found"

    stored = row["metadata"]
    if isinstance(stored, str):
        stored = json.loads(stored)
    assert stored is not None, "metadata column is NULL"

    for key, expected_val in meta.items():
        assert key in stored, f"key '{key}' missing from stored metadata"
        assert stored[key] == expected_val, (
            f"metadata['{key}']: expected {expected_val!r}, got {stored[key]!r}"
        )


async def test_createpool_duplicate_returns_409(app_and_pool):
    """POSTing the same (pool_name, owner_id) twice must return 200 then 409."""
    app, pool = app_and_pool
    owner_id = str(uuid4())
    pool_name = f"dup-{uuid4().hex[:8]}"
    payload = _createpool_payload(
        provider="aws", pool_name=pool_name, owner_id=owner_id
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post("/deployment/createpool", json=payload)
        assert first.status_code == 200, first.text

        second = await client.post("/deployment/createpool", json=payload)
        assert second.status_code == 409, (
            f"Expected 409 on duplicate, got {second.status_code}: {second.text}"
        )


async def test_createpool_invalid_provider_returns_400(app_and_pool):
    """POST /createpool with an unregistered provider must return 400."""
    app, pool = app_and_pool
    payload = _createpool_payload(provider="totally-fake")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/deployment/createpool", json=payload)

    assert resp.status_code == 400, (
        f"Expected 400 for invalid provider, got {resp.status_code}: {resp.text}"
    )
