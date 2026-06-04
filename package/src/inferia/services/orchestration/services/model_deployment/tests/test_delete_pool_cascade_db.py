"""Real-DB integration test for DELETE /deployment/pool/{id} cascade.

The fake-conn unit test (test_delete_pool_rest.py) asserts the SQL *shape*;
this one runs against a real Postgres to prove the cascade actually:
  * hard-deletes every deployment in the pool,
  * removes dependent inference_logs,
  * detaches (NULLs) policies.deployment_id / api_keys.deployment_id,
  * soft-deletes the pool row.

delete_pool_rest connects via the module-level POSTGRES_DSN (not app.state),
so we patch that to the test DSN and stub the audit/org lookups (which also
open their own connections).

Run with:
    TEST_DATABASE_URL=postgresql://inferia:inferia@localhost:5544/inferia_test \\
    python -m pytest .../tests/test_delete_pool_cascade_db.py
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

pytestmark = pytest.mark.asyncio

_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://inferia:inferia@localhost:5432/inferia_test",
)


@pytest_asyncio.fixture
async def pool():
    p = await asyncpg.create_pool(dsn=_DSN, min_size=1, max_size=4)
    yield p
    await p.close()


async def _seed(pool):
    """Seed org + active pool + one RUNNING deployment + one dependent row in
    policies / api_keys / inference_logs. Return (pool_id, deploy_id, org_id)."""
    org_id = str(uuid4())
    pool_id = uuid4()
    deploy_id = uuid4()
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO organizations(id, name) VALUES($1,$2) ON CONFLICT DO NOTHING",
            org_id, f"o-{org_id}",
        )
        await c.execute(
            """INSERT INTO compute_pools(
                   id, pool_name, owner_type, owner_id, provider, pool_type,
                   allowed_gpu_types, max_cost_per_hour, scheduling_policy,
                   provider_pool_id, is_active, lifecycle_state, gpu_count,
                   metadata)
               VALUES($1,$2,'organization',$3::text,'aws','cluster',
                      ARRAY['none'],0,'{}',$4,true,'running',1,'{}'::jsonb)""",
            pool_id, f"p-{pool_id}", org_id, f"placeholder:{pool_id}",
        )
        await c.execute(
            """INSERT INTO model_deployments(
                   deployment_id, model_name, replicas, gpu_per_replica,
                   pool_id, target_pool_id, state, org_id)
               VALUES($1,$2,1,1,$3,$3,'RUNNING',$4)""",
            deploy_id, f"m-{deploy_id}", pool_id, org_id,
        )
        # Dependent rows that lack ON DELETE behavior.
        await c.execute(
            "INSERT INTO inference_logs(id, deployment_id, user_id, model) "
            "VALUES($1,$2,'u','m')",
            f"log-{deploy_id}", deploy_id,
        )
        await c.execute(
            "INSERT INTO policies(id, policy_type, config_json, org_id, deployment_id) "
            "VALUES($1,'rate_limit','{}'::json,$2,$3)",
            f"pol-{deploy_id}", org_id, deploy_id,
        )
        await c.execute(
            "INSERT INTO api_keys(id, name, key_hash, prefix, org_id, deployment_id) "
            "VALUES($1,'k',$2,$3,$4,$5)",
            f"ak-{deploy_id}", f"hash-{deploy_id}", f"pre{str(deploy_id)[:6]}",
            org_id, deploy_id,
        )
    return pool_id, deploy_id, org_id


async def test_delete_pool_cascades_to_deployments(pool):
    pool_id, deploy_id, _ = await _seed(pool)

    app = FastAPI()
    app.include_router(deployment_server.router)

    with patch.object(deployment_server, "POSTGRES_DSN", _DSN), \
         patch.object(deployment_server, "log_audit_event", AsyncMock()), \
         patch.object(deployment_server, "_lookup_org_id",
                      AsyncMock(return_value=None)):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/deployment/pool/{pool_id}")

    assert resp.status_code == 202, resp.text

    async with pool.acquire() as c:
        # Deployment row is GONE.
        assert await c.fetchval(
            "SELECT count(*) FROM model_deployments WHERE deployment_id=$1",
            deploy_id,
        ) == 0
        # inference_logs row removed.
        assert await c.fetchval(
            "SELECT count(*) FROM inference_logs WHERE deployment_id=$1",
            deploy_id,
        ) == 0
        # policies / api_keys rows survive but are detached.
        assert await c.fetchval(
            "SELECT deployment_id FROM policies WHERE id=$1", f"pol-{deploy_id}",
        ) is None
        assert await c.fetchval(
            "SELECT deployment_id FROM api_keys WHERE id=$1", f"ak-{deploy_id}",
        ) is None
        # Pool soft-deleted.
        row = await c.fetchrow(
            "SELECT is_active, lifecycle_state FROM compute_pools WHERE id=$1",
            pool_id,
        )
        assert row["is_active"] is False


async def test_delete_pool_404_when_already_deleted(pool):
    """Soft-deleted (is_active=FALSE) pool → 404 (idempotent)."""
    pool_id, _, _ = await _seed(pool)
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE compute_pools SET is_active=FALSE WHERE id=$1", pool_id,
        )

    app = FastAPI()
    app.include_router(deployment_server.router)
    with patch.object(deployment_server, "POSTGRES_DSN", _DSN), \
         patch.object(deployment_server, "log_audit_event", AsyncMock()), \
         patch.object(deployment_server, "_lookup_org_id",
                      AsyncMock(return_value=None)):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/deployment/pool/{pool_id}")
    assert resp.status_code == 404
