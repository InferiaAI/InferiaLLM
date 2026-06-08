"""Unit tests for the extracted ``place_and_provision`` helper.

``place_and_provision`` is the placement+provisioning core lifted out of the
``/deploy`` handler so the resume (``/start``) path can reuse it. These tests
exercise the ColdStart non-worker branch with fully-mocked ``deps`` repos so we
never touch a real Postgres.

IMPORTANT (see MEMORY: AsyncMock signature blindness): a bare ``AsyncMock``
accepts any kwargs silently, so a wrong call signature passes green while
production raises ``TypeError``. We pin every repo mock with ``spec=RealClass``
AND assert on ``await_args.kwargs`` so a signature drift is caught.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from inferia.services.orchestration.services.model_deployment import (
    deployment_server,
)
from inferia.services.orchestration.services.model_deployment.deployment_server import (
    DeployModelRequest,
    place_and_provision,
    _model_spec_from_source,
)
from inferia.services.orchestration.services.model_deployment.pool_placer import (
    PoolPlacer,
    BindToReady,
    ColdStart,
)
from inferia.services.orchestration.repositories.inventory_repo import (
    InventoryRepository,
)
from inferia.services.orchestration.repositories.model_deployment_repo import (
    ModelDeploymentRepository,
)
from inferia.services.orchestration.services.provisioning.jobs.repository import (
    ProvisioningJobRepository,
)

pytestmark = pytest.mark.asyncio


class _AcquireCtx:
    """Async context manager returned by ``db_pool.acquire()``.

    The connection it yields is itself an async context manager (for
    ``async with conn.transaction()``) — we make ``conn.transaction()`` return
    a no-op async CM.
    """

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _TxCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


def _make_conn() -> MagicMock:
    conn = MagicMock(name="conn")
    conn.transaction = MagicMock(return_value=_TxCtx())
    return conn


def _make_db_pool() -> MagicMock:
    """A db_pool whose ``.acquire()`` yields a fresh mock connection."""
    conn = _make_conn()
    pool = MagicMock(name="db_pool")
    pool.acquire = MagicMock(return_value=_AcquireCtx(conn))
    return pool


def _load_spec_source(*, engine="vllm"):
    """A minimal stand-in for a DeployModelRequest carrying the fields the warm
    load-spec builder reads. ColdStart never builds a warm spec, but
    place_and_provision still needs the param."""
    return SimpleNamespace(
        engine=engine,
        configuration={"artifact_uri": "hf://test-model"},
        inference_model=None,
        model_name="test-model",
        gpu_per_replica=1,
    )


async def test_coldstart_non_worker_pool_creates_placeholder_and_enqueues_one_job(
    monkeypatch,
):
    """ColdStart on a non-worker pool:

    - creates exactly one placeholder node,
    - binds the deployment to it and sets state PENDING_NODE,
    - enqueues exactly ONE provisioning job,
    - returns (body, 202) with state PENDING_NODE.
    """
    deploy_id = uuid4()
    pool_id = uuid4()
    node_id = uuid4()

    # Avoid building a real AWS spec — _build_provisioning_spec hits the
    # instance catalog. We only care that exactly one job is enqueued.
    fake_spec = {"provider": "aws", "instance_type": "g6.xlarge"}

    async def _fake_build_spec(*, pool_row, pool_meta, decision, org_id):
        return fake_spec

    monkeypatch.setattr(
        deployment_server, "_build_provisioning_spec", _fake_build_spec
    )

    placer = AsyncMock(spec=PoolPlacer)
    placer.place.return_value = ColdStart(gpu_total_per_node=1, provider="aws")

    inventory = AsyncMock(spec=InventoryRepository)
    inventory.create_placeholder.return_value = node_id

    deploys = AsyncMock(spec=ModelDeploymentRepository)
    jobs_repo = AsyncMock(spec=ProvisioningJobRepository)

    deps = SimpleNamespace(
        db_pool=_make_db_pool(),
        controller=AsyncMock(),
        inventory=inventory,
        deploys=deploys,
        placer=placer,
        jobs_repo=jobs_repo,
    )

    body, status = await place_and_provision(
        deploy_id=deploy_id,
        pool_id=pool_id,
        pool_row={"id": pool_id},
        pool_meta={},  # no agent_kind => non-worker pool
        gpu_per_replica=1,
        org_id=str(uuid4()),
        engine="vllm",
        load_spec_source=_load_spec_source(),
        deps=deps,
    )

    # Response
    assert status == 202
    assert body["state"] == "PENDING_NODE"
    assert body["target_node_id"] == str(node_id)

    # Placeholder created exactly once, with the right signature.
    inventory.create_placeholder.assert_awaited_once()
    cp_kwargs = inventory.create_placeholder.await_args.kwargs
    assert cp_kwargs["pool_id"] == pool_id
    assert cp_kwargs["gpu_total"] == 1
    assert cp_kwargs["initial_alloc"] == 1

    # Bound + state transition.
    deploys.bind_to_node.assert_awaited_once()
    bind_args = deploys.bind_to_node.await_args
    assert bind_args.args[0] == deploy_id
    assert bind_args.args[1] == node_id

    deploys.set_state.assert_awaited_once()
    ss_args = deploys.set_state.await_args
    assert ss_args.args[0] == deploy_id
    assert ss_args.args[1] == "PENDING_NODE"

    # Exactly one provisioning job enqueued, with the committed node_id (FK).
    jobs_repo.enqueue.assert_awaited_once()
    eq_kwargs = jobs_repo.enqueue.await_args.kwargs
    assert eq_kwargs["node_id"] == node_id
    assert eq_kwargs["pool_id"] == pool_id
    assert eq_kwargs["provider"] == "aws"
    assert eq_kwargs["spec"] is fake_spec

    # No warm-path load_model on a ColdStart.
    deps.controller.load_model.assert_not_called()


async def test_coldstart_worker_pool_does_not_enqueue(monkeypatch):
    """ColdStart on a worker pool (agent_kind=worker): PENDING_NODE, no job."""
    deploy_id = uuid4()
    pool_id = uuid4()
    node_id = uuid4()

    placer = AsyncMock(spec=PoolPlacer)
    placer.place.return_value = ColdStart(gpu_total_per_node=1, provider="aws")
    inventory = AsyncMock(spec=InventoryRepository)
    inventory.create_placeholder.return_value = node_id
    deploys = AsyncMock(spec=ModelDeploymentRepository)
    jobs_repo = AsyncMock(spec=ProvisioningJobRepository)

    deps = SimpleNamespace(
        db_pool=_make_db_pool(),
        controller=AsyncMock(),
        inventory=inventory,
        deploys=deploys,
        placer=placer,
        jobs_repo=jobs_repo,
    )

    body, status = await place_and_provision(
        deploy_id=deploy_id,
        pool_id=pool_id,
        pool_row={"id": pool_id},
        pool_meta={"agent_kind": "worker"},
        gpu_per_replica=1,
        org_id=str(uuid4()),
        engine="vllm",
        load_spec_source=_load_spec_source(),
        deps=deps,
    )

    assert status == 202
    assert body["state"] == "PENDING_NODE"
    assert body["message"] == "waiting for worker registration"
    jobs_repo.enqueue.assert_not_awaited()


# ---------------------------------------------------------------------------
# _model_spec_from_source: the row-shape resolver that is the reason the helper
# exists. The deploy path passes a DeployModelRequest; the resume path passes a
# deployment DB row (dict, or asyncpg Record whose jsonb columns arrive as JSON
# strings). All three shapes must resolve to an identical model spec.
# ---------------------------------------------------------------------------


def _row_configuration() -> dict:
    """A realistic deployment-row ``configuration`` jsonb payload."""
    return {
        "model": {
            "artifact_uri": "s3://bucket/my-model",
            "format": "safetensors",
            "backend": "vllm",
        },
        "config": {"max_model_len": 4096},
    }


async def test_model_spec_from_source_plain_dict_row():
    """A plain dict 'row' (the simplest resume shape) resolves the nested
    configuration.model block into the worker load spec."""
    row = {
        "engine": "vllm",
        "configuration": _row_configuration(),
        "inference_model": None,
        "model_name": "my-model",
    }

    spec = _model_spec_from_source(row)

    assert spec == {
        "artifact_uri": "s3://bucket/my-model",
        "format": "safetensors",
        "backend": "vllm",
    }


async def test_model_spec_from_source_jsonb_string_decodes_identically():
    """asyncpg surfaces a jsonb column as a JSON *string*. The helper must
    json.loads it so the string row resolves byte-for-byte identically to the
    dict row."""
    cfg = _row_configuration()
    dict_row = {
        "engine": "vllm",
        "configuration": cfg,
        "inference_model": None,
        "model_name": "my-model",
    }
    string_row = {
        "engine": "vllm",
        "configuration": json.dumps(cfg),  # jsonb -> str under asyncpg
        "inference_model": None,
        "model_name": "my-model",
    }

    assert _model_spec_from_source(string_row) == _model_spec_from_source(
        dict_row
    )
    # And the decoded value is the real spec, not an empty/fallback shape.
    assert _model_spec_from_source(string_row) == {
        "artifact_uri": "s3://bucket/my-model",
        "format": "safetensors",
        "backend": "vllm",
    }


async def test_model_spec_from_source_request_row_parity():
    """PARITY: a DeployModelRequest (deploy path) and an equivalent dict row
    (resume path) must resolve to the SAME model spec — this is the whole point
    of _model_spec_from_source taking a source-agnostic shape."""
    cfg = _row_configuration()
    req = DeployModelRequest(
        model_name="my-model",
        model_version="1",
        replicas=1,
        gpu_per_replica=1,
        pool_id=str(uuid4()),
        engine="vllm",
        configuration=cfg,
        inference_model=None,
    )
    row = {
        "engine": "vllm",
        "configuration": cfg,
        "inference_model": None,
        "model_name": "my-model",
    }

    assert _model_spec_from_source(req) == _model_spec_from_source(row)


async def test_bind_to_ready_warm_path_fires_load_model(monkeypatch):
    """BindToReady (existing ready node has capacity): allocate_gpu succeeds,
    deployment binds + goes DEPLOYING, and post-tx ``controller.load_model``
    fires with the resolved model spec from ``load_spec_source``."""
    # Keep the post-tx warm path off the real mirror lookup.
    async def _no_mirror(*a, **k):
        return None

    monkeypatch.setattr(
        deployment_server, "resolve_and_apply_mirror", _no_mirror, raising=False
    )

    deploy_id = uuid4()
    pool_id = uuid4()
    node_id = uuid4()

    placer = AsyncMock(spec=PoolPlacer)
    placer.place.return_value = BindToReady(node_id=node_id)

    inventory = AsyncMock(spec=InventoryRepository)
    inventory.allocate_gpu.return_value = True  # capacity available -> warm path

    deploys = AsyncMock(spec=ModelDeploymentRepository)
    jobs_repo = AsyncMock(spec=ProvisioningJobRepository)
    controller = AsyncMock()

    deps = SimpleNamespace(
        db_pool=_make_db_pool(),
        controller=controller,
        inventory=inventory,
        deploys=deploys,
        placer=placer,
        jobs_repo=jobs_repo,
    )

    source = SimpleNamespace(
        engine="vllm",
        configuration=_row_configuration(),
        inference_model=None,
        model_name="my-model",
        gpu_per_replica=1,
    )

    body, status = await place_and_provision(
        deploy_id=deploy_id,
        pool_id=pool_id,
        pool_row={"id": pool_id},
        pool_meta={},
        gpu_per_replica=1,
        org_id=str(uuid4()),
        engine="vllm",
        load_spec_source=source,
        deps=deps,
    )

    assert status == 200
    assert body["state"] == "DEPLOYING"
    assert body["target_node_id"] == str(node_id)

    # Warm path: no provisioning job.
    jobs_repo.enqueue.assert_not_awaited()

    # load_model fires post-tx with the node + resolved model spec.
    controller.load_model.assert_awaited_once()
    lm_kwargs = controller.load_model.await_args.kwargs
    assert lm_kwargs["node_id"] == str(node_id)
    assert lm_kwargs["spec"]["model"] == {
        "artifact_uri": "s3://bucket/my-model",
        "format": "safetensors",
        "backend": "vllm",
    }
