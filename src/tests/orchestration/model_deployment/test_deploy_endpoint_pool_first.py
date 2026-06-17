"""Integration tests for the pool-first /deploy endpoint (T7).

These tests spin up an isolated FastAPI app with the deployment_server router,
set app.state.pool to a real asyncpg pool against inferia_test, and mock
app.state.worker_controller.  All DB state is seeded fresh per test (unique
UUIDs).

Run with:
    TEST_DATABASE_URL=postgresql://inferia:inferia@172.18.0.3:5432/inferia_test \\
    PYTHONPATH=src \\
    python -m pytest \\
      src/tests/orchestration/model_deployment/test_deploy_endpoint_pool_first.py -v
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock
from uuid import uuid4, UUID

import asyncpg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from orchestration.models.model_deployment import (
    deployment_server,
)
from orchestration.workers.worker_controller.controller import (
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
# Seed helpers
# ---------------------------------------------------------------------------

async def _seed_pool(
    pool,
    *,
    gpu_count: int = 4,
    max_nodes: int | None = None,
    provider: str = "aws",
    lifecycle_state: str = "running",
    metadata: dict | None = None,
    instance_type: str = "g6.xlarge",  # must be a real catalog type for AWS
    region: str = "us-east-1",
) -> UUID:
    """Insert a compute_pool row, return its UUID.

    For AWS, allowed_gpu_types[0] must be a real EC2 instance type that the
    instance catalog knows (g6.xlarge -> normal_gpu), and region_constraint
    must be set, or _build_provisioning_spec fails the deploy with 422.
    """
    org_id = uuid4()
    pool_id = uuid4()
    meta_json = json.dumps(metadata or {})
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO organizations(id, name) VALUES($1, $2) ON CONFLICT DO NOTHING",
            str(org_id), f"o-{org_id}",
        )
        await c.execute(
            """INSERT INTO compute_pools(
                   id, pool_name, owner_type, owner_id, provider, pool_type,
                   allowed_gpu_types, max_cost_per_hour, scheduling_policy,
                   provider_pool_id, is_active, lifecycle_state, gpu_count,
                   max_nodes, metadata, region_constraint
               )
               VALUES ($1, $2, 'organization', $3::text, $4, 'cluster',
                       ARRAY[$10], 0, '{}', $5, true, $6, $7, $8, $9::jsonb,
                       ARRAY[$11])""",
            pool_id, f"p-{pool_id}", str(org_id), provider,
            f"placeholder:{pool_id}", lifecycle_state, gpu_count, max_nodes,
            meta_json, instance_type, region,
        )
    return pool_id


async def _seed_node(
    pool,
    pool_id: UUID,
    *,
    gpu_total: int = 4,
    gpu_allocated: int = 0,
    state: str = "ready",
) -> UUID:
    """Insert a compute_inventory row, return its UUID."""
    node_id = uuid4()
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO compute_inventory(
                   id, pool_id, provider, provider_instance_id, hostname,
                   node_name, agent_kind, gpu_total, gpu_allocated,
                   vcpu_total, vcpu_allocated, ram_gb_total, ram_gb_allocated,
                   state
               )
               VALUES ($1, $2, 'aws', $3, 'h', $4, 'worker',
                       $5, $6, 0, 0, 0, 0, $7)""",
            node_id, pool_id, f"p-{node_id}", f"n-{node_id}",
            gpu_total, gpu_allocated, state,
        )
    return node_id


async def _seed_ready_node(
    pool,
    pool_id: UUID,
    *,
    gpu_total: int = 4,
    gpu_allocated: int = 0,
) -> UUID:
    """Alias of _seed_node with state='ready' for clarity."""
    return await _seed_node(pool, pool_id, gpu_total=gpu_total,
                            gpu_allocated=gpu_allocated, state="ready")


def _deploy_payload(pool_id: UUID, *, gpu_per_replica: int = 1) -> dict:
    return {
        "model_name": "test-model",
        "model_version": "v1",
        "replicas": 1,
        "gpu_per_replica": gpu_per_replica,
        "pool_id": str(pool_id),
        "engine": "vllm",
        # ami_id is required for vLLM deployments.
        "ami_id": "ami-0123456789abcdef0",
        # model_name doubles as artifact_uri fallback; provide explicit
        # configuration.artifact_uri so the spec helper can resolve it.
        "configuration": {"artifact_uri": "hf://test-model"},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_deploy_to_empty_pool_returns_pending_node(app_and_pool):
    """ColdStart path: empty pool => PENDING_NODE + one provisioning job + one placeholder."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/deployment/deploy", json=_deploy_payload(pool_id))

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["state"] == "PENDING_NODE"
    assert "target_node_id" in body
    deploy_id = UUID(body["deployment_id"])
    node_id = UUID(body["target_node_id"])

    async with pool.acquire() as c:
        deploy_row = await c.fetchrow(
            "SELECT state, target_pool_id, target_node_id FROM model_deployments "
            "WHERE deployment_id=$1",
            deploy_id,
        )
        assert deploy_row["state"] == "PENDING_NODE"
        assert deploy_row["target_pool_id"] == pool_id
        assert deploy_row["target_node_id"] == node_id

        job_count = await c.fetchval(
            "SELECT COUNT(*) FROM provisioning_jobs WHERE pool_id=$1", pool_id,
        )
        assert job_count == 1, "expected exactly one ProvisioningJob"

        inv_count = await c.fetchval(
            "SELECT COUNT(*) FROM compute_inventory WHERE pool_id=$1 AND state='provisioning'",
            pool_id,
        )
        assert inv_count == 1, "expected exactly one placeholder node"


async def test_deploy_to_warm_pool_returns_running(app_and_pool):
    """BindToReady path: pool with ready node => the model loads on the worker
    (status=ok) and the deploy is promoted to RUNNING, GPU allocated,
    load_model called. Previously this returned DEPLOYING and never promoted —
    a successful warm load stayed DEPLOYING forever."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)
    node_id = await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=0)

    # Worker reports the model loaded successfully.
    from orchestration.workers.worker_controller.protocol import (
        CommandResultBody,
    )
    app.state.worker_controller.load_model.return_value = CommandResultBody(
        in_reply_to="x", status="ok", detail="",
        endpoint_url="http://127.0.0.1:9000",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/deployment/deploy",
            json=_deploy_payload(pool_id, gpu_per_replica=2),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "RUNNING"
    assert UUID(body["target_node_id"]) == node_id

    async with pool.acquire() as c:
        inv_row = await c.fetchrow(
            "SELECT gpu_allocated FROM compute_inventory WHERE id=$1", node_id,
        )
        assert inv_row["gpu_allocated"] == 2

    controller = app.state.worker_controller
    controller.load_model.assert_awaited_once()
    call_kwargs = controller.load_model.await_args.kwargs
    assert "spec" in call_kwargs
    assert call_kwargs["spec"]["deployment_id"]  # non-empty
    assert call_kwargs["spec"]["model"]["artifact_uri"]  # non-empty


async def test_deploy_to_terminating_pool_returns_409(app_and_pool):
    """Deploying to a terminating pool must return 409."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool, lifecycle_state="terminating")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/deployment/deploy", json=_deploy_payload(pool_id))

    assert resp.status_code == 409, resp.text


async def test_deploy_at_max_nodes_returns_503(app_and_pool):
    """PoolAtCapacity path: pool at max_nodes returns 503 with POOL_AT_CAPACITY body."""
    app, pool = app_and_pool
    # max_nodes=1, node fully allocated so no free slot; adding another node blocked.
    pool_id = await _seed_pool(pool, gpu_count=4, max_nodes=1)
    await _seed_node(pool, pool_id, gpu_total=4, gpu_allocated=4)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/deployment/deploy", json=_deploy_payload(pool_id))

    assert resp.status_code == 503, resp.text
    assert resp.headers.get("retry-after") == "60"
    body = resp.json()
    assert body["error"] == "POOL_AT_CAPACITY"
    assert "current_nodes" in body
    assert "max_nodes" in body
    assert "deployment_id" in body


async def test_deploy_to_worker_pool_pending_no_provisioning_job(app_and_pool):
    """ColdStart with worker-pool metadata: PENDING_NODE returned, zero ProvisioningJobs."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool, metadata={"agent_kind": "worker"})

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/deployment/deploy", json=_deploy_payload(pool_id))

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["state"] == "PENDING_NODE"
    assert body.get("message") == "waiting for worker registration"

    async with pool.acquire() as c:
        job_count = await c.fetchval(
            "SELECT COUNT(*) FROM provisioning_jobs WHERE pool_id=$1", pool_id,
        )
    assert job_count == 0, "worker pool must NOT enqueue a ProvisioningJob"


async def test_deploy_duplicate_model_name_in_org_returns_409(app_and_pool):
    """Duplicate-name guard: same model_name + same org_id => 409 on second deploy."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool, gpu_count=4)
    await _seed_ready_node(pool, pool_id, gpu_total=4)
    org_id = str(uuid4())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First deploy succeeds (DEPLOYING — warm node present)
        r1 = await client.post("/deployment/deploy", json={
            "model_name": "qwen3",
            "model_version": "1.0",
            "replicas": 1,
            "gpu_per_replica": 1,
            "pool_id": str(pool_id),
            "engine": "vllm",
            "org_id": org_id,
            "ami_id": "ami-0123456789abcdef0",
            "configuration": {"artifact_uri": "hf://qwen3"},
        })
        assert r1.status_code == 200, r1.text

        # Second deploy with same model_name + same org => 409
        r2 = await client.post("/deployment/deploy", json={
            "model_name": "qwen3",
            "model_version": "1.0",
            "replicas": 1,
            "gpu_per_replica": 1,
            "pool_id": str(pool_id),
            "engine": "vllm",
            "org_id": org_id,
            "ami_id": "ami-0123456789abcdef0",
            "configuration": {"artifact_uri": "hf://qwen3"},
        })
        assert r2.status_code == 409, r2.text
        assert "already exists" in r2.text


# ---------------------------------------------------------------------------
# Unit tests for _build_provisioning_spec (the AWS spec builder)
# ---------------------------------------------------------------------------

class _Decision:
    """Minimal stand-in for PoolPlacer.ColdStart."""
    def __init__(self, provider="aws", gpu_total_per_node=1):
        self.provider = provider
        self.gpu_total_per_node = gpu_total_per_node


async def test_build_provisioning_spec_derives_instance_class_and_region():
    """instance_class is catalog-derived (single source of truth); region
    comes from the pool's region_constraint."""
    pool_row = {
        "id": uuid4(),
        "allowed_gpu_types": ["g6.xlarge"],
        "provider_pool_id": "aws/g6.xlarge",
        "region_constraint": ["us-east-1"],
    }
    spec = await deployment_server._build_provisioning_spec(
        pool_row=pool_row, pool_meta={}, decision=_Decision(), org_id="org-1",
    )
    assert spec["provider"] == "aws"
    assert spec["instance_type"] == "g6.xlarge"
    assert spec["instance_class"] == "normal_gpu"  # catalog-derived
    assert spec["region"] == "us-east-1"
    assert spec["gpu_count"] == 1
    assert spec["root_volume_gb"] == 130  # GPU DLAMI needs >=75GB


async def test_build_provisioning_spec_unknown_instance_type_raises_422():
    from fastapi import HTTPException
    pool_row = {
        "id": uuid4(),
        "allowed_gpu_types": ["totally-fake.42xlarge"],
        "provider_pool_id": "aws/totally-fake.42xlarge",
        "region_constraint": ["us-east-1"],
    }
    with pytest.raises(HTTPException) as exc:
        await deployment_server._build_provisioning_spec(
            pool_row=pool_row, pool_meta={}, decision=_Decision(), org_id="o",
        )
    assert exc.value.status_code == 422
    assert "catalog" in str(exc.value.detail).lower()


async def test_build_provisioning_spec_includes_pool_metadata_overrides():
    pool_row = {
        "id": uuid4(),
        "allowed_gpu_types": ["g6.xlarge"],
        "provider_pool_id": "aws/g6.xlarge",
        "region_constraint": ["us-west-2"],
    }
    meta = {
        "subnet_id": "subnet-abc",
        "security_group_ids": ["sg-123"],
        "iam_instance_profile": "arn:aws:iam::1:instance-profile/x",
        "root_volume_gb": 200,
    }
    spec = await deployment_server._build_provisioning_spec(
        pool_row=pool_row, pool_meta=meta, decision=_Decision(), org_id="o",
    )
    assert spec["subnet_id"] == "subnet-abc"
    assert spec["security_group_ids"] == ["sg-123"]
    assert spec["iam_instance_profile"].endswith("/x")
    assert spec["root_volume_gb"] == 200  # explicit override wins over GPU default


async def test_build_spec_aws_requires_region():
    """_build_provisioning_spec raises 422 when pool has no region at all.

    region_constraint=None + empty pool_meta means no account-wide fallback
    is available (that fallback was removed); must surface a clear 422 so
    the operator knows to set region_constraint at pool creation.
    """
    from fastapi import HTTPException

    decision = _Decision(provider="aws", gpu_total_per_node=1)

    # No region_constraint, no pool_meta.region — no fallback remains.
    pool_row = {
        "id": uuid4(),
        "allowed_gpu_types": ["g6.xlarge"],
        "provider_pool_id": "aws/g6.xlarge",
        "region_constraint": None,
    }
    with pytest.raises(HTTPException) as ei:
        await deployment_server._build_provisioning_spec(
            pool_row=pool_row, pool_meta={}, decision=decision, org_id=None
        )
    assert ei.value.status_code == 422
    assert "region" in str(ei.value.detail).lower()


async def test_build_spec_aws_requires_region_empty_list():
    """region_constraint=[] (empty list) is treated the same as None."""
    from fastapi import HTTPException

    decision = _Decision(provider="aws", gpu_total_per_node=1)
    pool_row = {
        "id": uuid4(),
        "allowed_gpu_types": ["g6.xlarge"],
        "provider_pool_id": "aws/g6.xlarge",
        "region_constraint": [],
    }
    with pytest.raises(HTTPException) as ei:
        await deployment_server._build_provisioning_spec(
            pool_row=pool_row, pool_meta={}, decision=decision, org_id=None
        )
    assert ei.value.status_code == 422
    assert "region" in str(ei.value.detail).lower()


async def test_build_spec_aws_falls_back_to_pool_meta_region():
    """pool_meta.region is used when region_constraint is absent."""
    decision = _Decision(provider="aws", gpu_total_per_node=1)
    pool_row = {
        "id": uuid4(),
        "allowed_gpu_types": ["g6.xlarge"],
        "provider_pool_id": "aws/g6.xlarge",
        "region_constraint": None,
    }
    spec = await deployment_server._build_provisioning_spec(
        pool_row=pool_row, pool_meta={"region": "eu-west-1"}, decision=decision, org_id="o"
    )
    assert spec["region"] == "eu-west-1"


# ---------------------------------------------------------------------------
# Tests for ami_id + hf_token_name (T5 provider-config-ux)
# ---------------------------------------------------------------------------


async def test_deploy_vllm_requires_ami_id(app_and_pool):
    """vLLM deploy without ami_id → 422 with 'ami_id' in the message."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)
    org_id = str(uuid4())

    body = {
        "model_name": "vllm-model",
        "model_version": "latest",
        "replicas": 1,
        "gpu_per_replica": 1,
        "pool_id": str(pool_id),
        "engine": "vllm",
        "org_id": org_id,
        # ami_id intentionally absent
        "configuration": {"artifact_uri": "hf://vllm-model"},
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post("/deployment/deploy", json=body)

    assert r.status_code == 422, r.text
    assert "ami_id" in r.text.lower()


async def test_deploy_vllm_with_ami_id_succeeds(app_and_pool):
    """vLLM deploy WITH ami_id proceeds past validation (PENDING_NODE on empty pool)."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)
    org_id = str(uuid4())

    body = {
        "model_name": "vllm-model-ami",
        "model_version": "latest",
        "replicas": 1,
        "gpu_per_replica": 1,
        "pool_id": str(pool_id),
        "engine": "vllm",
        "org_id": org_id,
        "ami_id": "ami-0123456789abcdef0",
        "configuration": {"artifact_uri": "hf://vllm-model-ami"},
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post("/deployment/deploy", json=body)

    # ColdStart path → 202 PENDING_NODE (ami_id validation passed)
    assert r.status_code == 202, r.text
    assert r.json()["state"] == "PENDING_NODE"


async def test_deploy_non_vllm_engine_no_ami_id_allowed(app_and_pool):
    """Non-vLLM engines (ollama) do not require ami_id."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)
    org_id = str(uuid4())

    body = {
        "model_name": "ollama-model",
        "model_version": "latest",
        "replicas": 1,
        "gpu_per_replica": 1,
        "pool_id": str(pool_id),
        "engine": "ollama",
        "org_id": org_id,
        # no ami_id — must be accepted for non-vLLM
        "configuration": {"artifact_uri": "ollama://llama3"},
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post("/deployment/deploy", json=body)

    # Any non-422 means we passed ami_id validation (real result may vary)
    assert r.status_code != 422, r.text


async def test_build_provisioning_spec_ami_id_override():
    """ami_id kwarg takes precedence over pool_meta.ami_id in the spec."""
    pool_row = {
        "id": uuid4(),
        "allowed_gpu_types": ["g6.xlarge"],
        "provider_pool_id": "aws/g6.xlarge",
        "region_constraint": ["us-east-1"],
    }
    # pool_meta has a stale pool-level ami_id
    meta = {"ami_id": "ami-pool-level"}
    spec = await deployment_server._build_provisioning_spec(
        pool_row=pool_row,
        pool_meta=meta,
        decision=_Decision(),
        org_id="o",
        ami_id="ami-deploy-level",
    )
    # deploy-level ami_id wins
    assert spec["ami_id"] == "ami-deploy-level"


async def test_build_provisioning_spec_ami_id_absent_when_none():
    """When ami_id is None and pool_meta has no ami_id, the key is absent
    from the spec so resolve_ami's auto-pick still applies."""
    pool_row = {
        "id": uuid4(),
        "allowed_gpu_types": ["g6.xlarge"],
        "provider_pool_id": "aws/g6.xlarge",
        "region_constraint": ["us-east-1"],
    }
    spec = await deployment_server._build_provisioning_spec(
        pool_row=pool_row,
        pool_meta={},
        decision=_Decision(),
        org_id="o",
        ami_id=None,
    )
    assert "ami_id" not in spec


# ---------------------------------------------------------------------------
# Unit tests for HF token injection in deploy_model
# ---------------------------------------------------------------------------

async def test_deploy_hf_token_name_injects_hf_token(app_and_pool):
    """hf_token_name resolves server-side and injects HF_TOKEN into
    configuration.env for the worker. The raw token never leaves the server."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)
    org_id = str(uuid4())

    # Seed a ready node so we get a warm-path DEPLOYING and can introspect
    # the load_model call's spec.env for HF_TOKEN.
    node_id = await _seed_ready_node(pool, pool_id, gpu_total=4)

    from orchestration.workers.worker_controller.protocol import (
        CommandResultBody,
    )
    app.state.worker_controller.load_model.return_value = CommandResultBody(
        in_reply_to="x", status="ok", detail="",
        endpoint_url="http://127.0.0.1:9001",
    )

    # Patch resolve_hf_token so no real provider config is needed.
    from unittest.mock import patch

    from unittest.mock import AsyncMock as _AsyncMock
    with patch(
        "orchestration.models.model_deployment"
        ".hf_token_resolver.resolve_hf_token",
        new_callable=_AsyncMock,
        return_value="hf_secret_tok",
    ):
        body_payload = {
            "model_name": "hf-model-inject",
            "model_version": "v1",
            "replicas": 1,
            "gpu_per_replica": 1,
            "pool_id": str(pool_id),
            "engine": "vllm",
            "org_id": org_id,
            "ami_id": "ami-0abc",
            "hf_token_name": "prod-token",
            "configuration": {"artifact_uri": "hf://gated-model"},
        }
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post("/deployment/deploy", json=body_payload)

    assert r.status_code == 200, r.text
    # Confirm load_model was called with HF_TOKEN in spec.env
    call_kwargs = app.state.worker_controller.load_model.await_args.kwargs
    spec_env = call_kwargs["spec"].get("env", {})
    assert spec_env.get("HF_TOKEN") == "hf_secret_tok", (
        f"HF_TOKEN not injected; env was: {spec_env}"
    )


async def test_deploy_hf_token_name_does_not_clobber_explicit_hf_token(app_and_pool):
    """If configuration.env already carries HF_TOKEN, hf_token_name must not
    overwrite it (setdefault semantics)."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)
    org_id = str(uuid4())
    await _seed_ready_node(pool, pool_id, gpu_total=4)

    from orchestration.workers.worker_controller.protocol import (
        CommandResultBody,
    )
    app.state.worker_controller.load_model.return_value = CommandResultBody(
        in_reply_to="x", status="ok", detail="",
        endpoint_url="http://127.0.0.1:9002",
    )

    from unittest.mock import patch

    from unittest.mock import AsyncMock as _AsyncMock
    with patch(
        "orchestration.models.model_deployment"
        ".hf_token_resolver.resolve_hf_token",
        new_callable=_AsyncMock,
        return_value="hf_from_name",
    ):
        body_payload = {
            "model_name": "hf-model-noclobber",
            "model_version": "v1",
            "replicas": 1,
            "gpu_per_replica": 1,
            "pool_id": str(pool_id),
            "engine": "vllm",
            "org_id": org_id,
            "ami_id": "ami-0abc",
            "hf_token_name": "prod-token",
            # Explicit HF_TOKEN in env — must NOT be replaced by the resolver
            "configuration": {
                "artifact_uri": "hf://gated-model",
                "env": {"HF_TOKEN": "hf_explicit"},
            },
        }
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post("/deployment/deploy", json=body_payload)

    assert r.status_code == 200, r.text
    call_kwargs = app.state.worker_controller.load_model.await_args.kwargs
    spec_env = call_kwargs["spec"].get("env", {})
    assert spec_env.get("HF_TOKEN") == "hf_explicit", (
        f"Explicit HF_TOKEN was clobbered; env: {spec_env}"
    )


async def test_deploy_hf_token_name_not_found_no_injection(app_and_pool):
    """If resolve_hf_token returns None (token not found), no HF_TOKEN is
    injected — the deploy proceeds normally without it."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)
    org_id = str(uuid4())
    await _seed_ready_node(pool, pool_id, gpu_total=4)

    from orchestration.workers.worker_controller.protocol import (
        CommandResultBody,
    )
    app.state.worker_controller.load_model.return_value = CommandResultBody(
        in_reply_to="x", status="ok", detail="",
        endpoint_url="http://127.0.0.1:9003",
    )

    from unittest.mock import patch

    from unittest.mock import AsyncMock as _AsyncMock
    with patch(
        "orchestration.models.model_deployment"
        ".hf_token_resolver.resolve_hf_token",
        new_callable=_AsyncMock,
        return_value=None,
    ):
        body_payload = {
            "model_name": "hf-model-notfound",
            "model_version": "v1",
            "replicas": 1,
            "gpu_per_replica": 1,
            "pool_id": str(pool_id),
            "engine": "vllm",
            "org_id": org_id,
            "ami_id": "ami-0abc",
            "hf_token_name": "nonexistent-token",
            "configuration": {"artifact_uri": "hf://public-model"},
        }
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post("/deployment/deploy", json=body_payload)

    assert r.status_code == 200, r.text
    call_kwargs = app.state.worker_controller.load_model.await_args.kwargs
    spec_env = call_kwargs["spec"].get("env", {})
    assert "HF_TOKEN" not in spec_env


# ---------------------------------------------------------------------------
# Tests for ami_id persistence in configuration (completeness fix)
# ---------------------------------------------------------------------------


async def test_deploy_vllm_ami_id_persisted_in_configuration(app_and_pool):
    """ami_id is stashed in configuration at deploy time so /start resume can
    reuse the operator-selected AMI instead of falling back to resolve_ami.

    Verify: after a successful /deploy with ami_id='ami-x', the DB row's
    configuration jsonb contains ``ami_id == 'ami-x'``.
    """
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)
    org_id = str(uuid4())

    body_payload = {
        "model_name": "ami-persist-test",
        "model_version": "v1",
        "replicas": 1,
        "gpu_per_replica": 1,
        "pool_id": str(pool_id),
        "engine": "vllm",
        "org_id": org_id,
        "ami_id": "ami-persist123",
        "configuration": {"artifact_uri": "hf://ami-persist-model"},
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post("/deployment/deploy", json=body_payload)

    # ColdStart => 202 PENDING_NODE
    assert r.status_code == 202, r.text
    deploy_id = UUID(r.json()["deployment_id"])

    async with pool.acquire() as c:
        raw_cfg = await c.fetchval(
            "SELECT configuration FROM model_deployments WHERE deployment_id=$1",
            deploy_id,
        )

    import json as _json
    cfg = _json.loads(raw_cfg) if isinstance(raw_cfg, str) else raw_cfg
    assert isinstance(cfg, dict), f"configuration is not a dict: {cfg!r}"
    assert cfg.get("ami_id") == "ami-persist123", (
        f"ami_id not persisted in configuration; got: {cfg}"
    )


async def test_deploy_vllm_ami_id_in_configuration_alongside_hf_token(app_and_pool):
    """When both ami_id and hf_token_name are provided, both land in
    configuration: configuration['ami_id'] == req.ami_id and
    configuration['env']['HF_TOKEN'] == resolved token."""
    app, pool = app_and_pool
    pool_id = await _seed_pool(pool)
    org_id = str(uuid4())
    await _seed_ready_node(pool, pool_id, gpu_total=4)

    from orchestration.workers.worker_controller.protocol import (
        CommandResultBody,
    )
    app.state.worker_controller.load_model.return_value = CommandResultBody(
        in_reply_to="x", status="ok", detail="",
        endpoint_url="http://127.0.0.1:9010",
    )

    from unittest.mock import patch

    from unittest.mock import AsyncMock as _AsyncMock
    with patch(
        "orchestration.models.model_deployment"
        ".hf_token_resolver.resolve_hf_token",
        new_callable=_AsyncMock,
        return_value="hf_combined_tok",
    ):
        body_payload = {
            "model_name": "ami-hf-combined",
            "model_version": "v1",
            "replicas": 1,
            "gpu_per_replica": 1,
            "pool_id": str(pool_id),
            "engine": "vllm",
            "org_id": org_id,
            "ami_id": "ami-combined456",
            "hf_token_name": "prod-token",
            "configuration": {"artifact_uri": "hf://gated-combined"},
        }
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post("/deployment/deploy", json=body_payload)

    assert r.status_code == 200, r.text
    deploy_id = UUID(r.json()["deployment_id"])

    import json as _json
    async with pool.acquire() as c:
        raw_cfg = await c.fetchval(
            "SELECT configuration FROM model_deployments WHERE deployment_id=$1",
            deploy_id,
        )
    cfg = _json.loads(raw_cfg) if isinstance(raw_cfg, str) else raw_cfg
    assert isinstance(cfg, dict)
    assert cfg.get("ami_id") == "ami-combined456", (
        f"ami_id not persisted; configuration={cfg}"
    )
    assert cfg.get("env", {}).get("HF_TOKEN") == "hf_combined_tok", (
        f"HF_TOKEN not persisted alongside ami_id; configuration={cfg}"
    )
