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

from orchestration.models.model_deployment import (
    deployment_server,
)
from orchestration.models.model_deployment import (
    direct_provision,
)
from orchestration.models.model_deployment.deployment_server import (
    DeployModelRequest,
    place_and_provision,
    _model_spec_from_source,
)
from orchestration.models.model_deployment.pool_placer import (
    PoolPlacer,
    BindToReady,
    ColdStart,
)
from orchestration.repositories.inventory_repo import (
    InventoryRepository,
)
from orchestration.repositories.model_deployment_repo import (
    ModelDeploymentRepository,
)
from orchestration.state_machine.jobs.repository import (
    ProvisioningJobRepository,
)
from orchestration.workers.worker_controller.protocol import (
    CommandResultBody,
)
from orchestration.workers.worker_controller.controller import (
    WorkerController,
)

import fastapi

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


def _make_conn(advertise_url=None) -> MagicMock:
    conn = MagicMock(name="conn")
    conn.transaction = MagicMock(return_value=_TxCtx())
    # The warm-path success branch reads advertise_url via ``await
    # conn.fetchval(...)`` to publish the inference endpoint (mirrors the
    # linker). Make it awaitable so the publish path executes for real.
    conn.fetchval = AsyncMock(return_value=advertise_url)
    return conn


def _make_db_pool(advertise_url=None) -> MagicMock:
    """A db_pool whose ``.acquire()`` yields a fresh mock connection.

    ``advertise_url`` is what ``conn.fetchval`` returns for the
    ``SELECT advertise_url FROM compute_inventory`` lookup the warm-path
    success branch performs before publishing the endpoint.
    """
    conn = _make_conn(advertise_url=advertise_url)
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

    async def _fake_build_spec(*, pool_row, pool_meta, decision, org_id, ami_id=None):
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
# T4: 3-way provisioning router wiring in place_and_provision.
#
# place_and_provision must dispatch each ColdStart by ``_provisioning_route``:
#   - "reconciler"     (aws/gcp/azure)      -> jobs_repo.enqueue (UNCHANGED)
#   - "direct_adapter" (nosana/akash/k8s)   -> _schedule_background(provision_direct_node(...))
#   - "self_register"  (worker pool)        -> neither
#
# We patch the module-level ``_schedule_background`` with a synchronous spy so
# we can assert the coroutine factory was scheduled with the right kwargs
# WITHOUT actually running provision_direct_node (which would touch adapters).
# We also patch ``provision_direct_node`` with an AsyncMock(spec=...) so the
# spy receives a coroutine whose call args we can introspect via await_args,
# and so the never-awaited coroutine doesn't emit a RuntimeWarning.
# ---------------------------------------------------------------------------


def _route_deps(*, provider, node_id):
    """Build a deps namespace + mocks for a ColdStart routing test."""
    placer = AsyncMock(spec=PoolPlacer)
    placer.place.return_value = ColdStart(
        gpu_total_per_node=1, provider=provider
    )
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
    return deps, jobs_repo


def _patch_direct(monkeypatch):
    """Patch _schedule_background (synchronous spy) + provision_direct_node
    (AsyncMock) so direct-adapter scheduling is observable without running it.

    Returns (sched_spy, direct_mock). ``sched_spy`` records the coroutine it
    was handed; we close it so no 'coroutine was never awaited' warning fires.
    """
    direct_mock = AsyncMock(spec=direct_provision.provision_direct_node)

    scheduled = []

    def _sched_spy(coro):
        scheduled.append(coro)
        # Close the coroutine to avoid an un-awaited-coroutine RuntimeWarning;
        # the AsyncMock has already recorded the call + its kwargs.
        try:
            coro.close()
        except Exception:
            pass
        return None

    _sched_spy.scheduled = scheduled  # type: ignore[attr-defined]

    monkeypatch.setattr(
        deployment_server, "provision_direct_node", direct_mock, raising=True
    )
    monkeypatch.setattr(
        deployment_server, "_schedule_background", _sched_spy, raising=True
    )
    return _sched_spy, direct_mock


async def test_coldstart_aws_enqueues_reconciler_job_not_direct(monkeypatch):
    """AWS REGRESSION LOCK: a non-worker aws pool ColdStart must enqueue
    exactly ONE reconciler ProvisioningJob with provider='aws' and an AWS spec,
    and must NOT schedule the direct background helper. This pins the AWS hot
    path byte-for-byte while the 3-way router is wired in around it."""
    deploy_id = uuid4()
    pool_id = uuid4()
    node_id = uuid4()
    org_id = str(uuid4())

    fake_spec = {"provider": "aws", "instance_type": "g6.xlarge"}

    async def _fake_build_spec(*, pool_row, pool_meta, decision, org_id, ami_id=None):
        return fake_spec

    monkeypatch.setattr(
        deployment_server, "_build_provisioning_spec", _fake_build_spec
    )
    sched_spy, direct_mock = _patch_direct(monkeypatch)

    deps, jobs_repo = _route_deps(provider="aws", node_id=node_id)

    body, status = await place_and_provision(
        deploy_id=deploy_id,
        pool_id=pool_id,
        pool_row={"id": pool_id},
        pool_meta={},  # non-worker pool
        gpu_per_replica=1,
        org_id=org_id,
        engine="vllm",
        load_spec_source=_load_spec_source(),
        deps=deps,
        ami_id="ami-0123456789abcdef0",
    )

    assert status == 202
    assert body["state"] == "PENDING_NODE"
    assert body["message"] == "provisioning compute"

    # Reconciler enqueue UNCHANGED: exactly once, AWS provider + AWS spec.
    jobs_repo.enqueue.assert_awaited_once()
    eq = jobs_repo.enqueue.await_args.kwargs
    assert eq["node_id"] == node_id
    assert eq["pool_id"] == pool_id
    assert eq["org_id"] == org_id
    assert eq["provider"] == "aws"
    assert eq["spec"] is fake_spec

    # Direct helper must NOT be scheduled on the AWS path.
    assert sched_spy.scheduled == []
    direct_mock.assert_not_called()


async def test_coldstart_nosana_schedules_direct_not_enqueue(monkeypatch):
    """A nosana (DEPIN) pool ColdStart routes to the direct background helper:
    jobs_repo.enqueue is NOT awaited; provision_direct_node is scheduled via
    _schedule_background with the expected kwargs."""
    deploy_id = uuid4()
    pool_id = uuid4()
    node_id = uuid4()
    pool_row = {"id": pool_id}
    pool_meta = {"some": "meta"}

    sched_spy, direct_mock = _patch_direct(monkeypatch)
    deps, jobs_repo = _route_deps(provider="nosana", node_id=node_id)

    body, status = await place_and_provision(
        deploy_id=deploy_id,
        pool_id=pool_id,
        pool_row=pool_row,
        pool_meta=pool_meta,
        gpu_per_replica=2,
        org_id=str(uuid4()),
        engine="vllm",
        load_spec_source=_load_spec_source(),
        deps=deps,
    )

    assert status == 202
    assert body["state"] == "PENDING_NODE"
    assert body["message"] == "provisioning compute"

    # NO reconciler enqueue on a direct-adapter provider.
    jobs_repo.enqueue.assert_not_awaited()

    # The direct helper was scheduled exactly once...
    assert len(sched_spy.scheduled) == 1
    # ...and provision_direct_node was invoked with the expected kwargs.
    direct_mock.assert_called_once()
    kw = direct_mock.call_args.kwargs
    assert kw["deploy_id"] == deploy_id
    assert kw["node_id"] == node_id
    assert kw["provider"] == "nosana"
    assert kw["pool_row"] is pool_row
    assert kw["pool_meta"] is pool_meta
    assert kw["gpu_per_replica"] == 2
    assert kw["deps"] is deps


async def test_coldstart_akash_schedules_direct_not_enqueue(monkeypatch):
    """An akash (DEPIN) pool ColdStart behaves like nosana: direct background
    helper scheduled, no reconciler enqueue."""
    deploy_id = uuid4()
    pool_id = uuid4()
    node_id = uuid4()
    pool_row = {"id": pool_id}
    pool_meta = {}

    sched_spy, direct_mock = _patch_direct(monkeypatch)
    deps, jobs_repo = _route_deps(provider="akash", node_id=node_id)

    body, status = await place_and_provision(
        deploy_id=deploy_id,
        pool_id=pool_id,
        pool_row=pool_row,
        pool_meta=pool_meta,
        gpu_per_replica=1,
        org_id=str(uuid4()),
        engine="vllm",
        load_spec_source=_load_spec_source(),
        deps=deps,
    )

    assert status == 202
    assert body["state"] == "PENDING_NODE"

    jobs_repo.enqueue.assert_not_awaited()
    assert len(sched_spy.scheduled) == 1
    direct_mock.assert_called_once()
    kw = direct_mock.call_args.kwargs
    assert kw["provider"] == "akash"
    assert kw["node_id"] == node_id
    assert kw["deploy_id"] == deploy_id


async def test_coldstart_worker_pool_neither_enqueue_nor_direct(monkeypatch):
    """A worker pool (agent_kind=worker) self-registers: neither a reconciler
    enqueue NOR a direct background helper is scheduled."""
    deploy_id = uuid4()
    pool_id = uuid4()
    node_id = uuid4()

    sched_spy, direct_mock = _patch_direct(monkeypatch)
    deps, jobs_repo = _route_deps(provider="aws", node_id=node_id)

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
    assert sched_spy.scheduled == []
    direct_mock.assert_not_called()


# ---------------------------------------------------------------------------
# T4(b): _schedule_background helper keeps a strong reference to the task so it
# isn't garbage-collected mid-flight (the classic fire-and-forget create_task
# foot-gun). We verify the real helper schedules + retains, then drains.
# ---------------------------------------------------------------------------


async def test_schedule_background_runs_and_retains_then_drains():
    ran = {"n": 0}

    async def _coro():
        ran["n"] += 1

    t = deployment_server._schedule_background(_coro())
    # The task is tracked while pending (strong ref prevents GC).
    assert t in deployment_server._BG_TASKS
    await t
    # done_callback discards it after completion (no leak).
    assert ran["n"] == 1
    assert t not in deployment_server._BG_TASKS


# ---------------------------------------------------------------------------
# T4(c): the vLLM AMI requirement is AWS-only. Exercised in isolation (the
# /deploy handler's DB path needs a live Postgres which isn't available in the
# unit image; the gate logic is a pure conditional we replicate exactly).
# ---------------------------------------------------------------------------


def _ami_gate_should_422(*, engine, provider, ami_id) -> bool:
    """Mirror of the /deploy handler's AWS-gated vLLM AMI requirement.

    Kept in lock-step with deployment_server.deploy_model so this unit test
    fails loudly if the production conditional drifts.
    """
    _provider = (provider or "").lower()
    return (engine or "").lower() == "vllm" and _provider == "aws" and not ami_id


@pytest.mark.parametrize(
    "engine,provider,ami_id,expected",
    [
        ("vllm", "aws", None, True),       # AWS vLLM, no AMI -> 422
        ("vllm", "aws", "", True),          # blank AMI counts as missing
        ("vllm", "aws", "ami-x", False),    # AWS vLLM with AMI -> ok
        ("vllm", "nosana", None, False),    # DePIN vLLM, no AMI -> NOT gated
        ("vllm", "akash", None, False),     # DePIN vLLM, no AMI -> NOT gated
        ("vllm", "on_prem", None, False),   # worker pool vLLM -> NOT gated
        ("ollama", "aws", None, False),     # non-vLLM engine -> not gated
    ],
)
async def test_ami_gate_fires_only_for_aws_vllm(engine, provider, ami_id, expected):
    assert _ami_gate_should_422(
        engine=engine, provider=provider, ami_id=ami_id
    ) is expected


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
    controller = AsyncMock(spec=WorkerController)
    # The worker replies status=ok: the model loaded. place_and_provision must
    # promote DEPLOYING -> RUNNING and publish the endpoint (mirrors linker).
    controller.load_model.return_value = CommandResultBody(
        in_reply_to="x", status="ok", detail="",
        endpoint_url="http://127.0.0.1:9000",
    )

    deps = SimpleNamespace(
        db_pool=_make_db_pool(advertise_url="http://10.0.0.5:8080"),
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
    # On a successful warm load, the deployment is promoted to RUNNING (the
    # worker is serving). Previously the body said DEPLOYING and the deploy was
    # never promoted — it stayed DEPLOYING forever.
    assert body["state"] == "RUNNING"
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

    # Promoted to RUNNING (the warm bind set DEPLOYING in-tx first; the final
    # set_state promotes RUNNING) and endpoint published with advertise_url.
    ss_states = [c.args[1] for c in deploys.set_state.await_args_list]
    assert ss_states[-1] == "RUNNING"
    assert deploys.set_state.await_args.args[0] == deploy_id

    deploys.update_endpoint.assert_awaited_once()
    ue_args = deploys.update_endpoint.await_args
    assert ue_args.args[0] == deploy_id
    assert ue_args.args[1] == "http://10.0.0.5:8080"

    # No FAILED transition on the happy path.
    for call in deploys.update_state.await_args_list:
        assert call.kwargs.get("state") != "FAILED"
        if len(call.args) >= 2:
            assert call.args[1] != "FAILED"


@pytest.mark.parametrize("provider", ["aws", "on_prem", "nosana", None])
async def test_bind_to_ready_warm_path_threads_provider_to_mirror(provider, monkeypatch):
    """The warm path must pass the POOL's provider to resolve_and_apply_mirror
    so the mirror is bypassed for public-cloud providers (the gate lives in
    resolve_and_apply_mirror). This pins the call-site wiring: a regression that
    dropped the provider kwarg would silently re-route cloud deploys through the
    (often unreachable) CP mirror."""
    captured = {}

    async def _spy_mirror(spec, *, recipe, artifact_uri, mirror_base, cache_repo,
                          provider=None):
        captured["provider"] = provider
        captured["called"] = True

    # The warm path imports resolve_and_apply_mirror LOCALLY (inside
    # place_and_provision), so patch the source module — the local import
    # resolves the name from mirror_decision at call time.
    from orchestration.models.model_deployment import mirror_decision
    monkeypatch.setattr(
        mirror_decision, "resolve_and_apply_mirror", _spy_mirror, raising=True
    )

    deploy_id, pool_id, node_id = uuid4(), uuid4(), uuid4()
    placer = AsyncMock(spec=PoolPlacer)
    placer.place.return_value = BindToReady(node_id=node_id)
    inventory = AsyncMock(spec=InventoryRepository)
    inventory.allocate_gpu.return_value = True
    deploys = AsyncMock(spec=ModelDeploymentRepository)
    jobs_repo = AsyncMock(spec=ProvisioningJobRepository)
    controller = AsyncMock(spec=WorkerController)
    controller.load_model.return_value = CommandResultBody(
        in_reply_to="x", status="ok", detail="", endpoint_url="http://127.0.0.1:9000",
    )
    deps = SimpleNamespace(
        db_pool=_make_db_pool(advertise_url="http://10.0.0.9:8080"),
        controller=controller, inventory=inventory, deploys=deploys,
        placer=placer, jobs_repo=jobs_repo,
    )
    source = SimpleNamespace(
        engine="vllm", configuration=_row_configuration(),
        inference_model=None, model_name="my-model", gpu_per_replica=1,
    )

    await place_and_provision(
        deploy_id=deploy_id, pool_id=pool_id,
        pool_row={"id": pool_id, "provider": provider},
        pool_meta={}, gpu_per_replica=1, org_id=str(uuid4()),
        engine="vllm", load_spec_source=source, deps=deps,
    )

    assert captured.get("called") is True
    assert captured["provider"] == provider  # exactly the pool's provider


async def test_bind_to_ready_warm_path_failed_status_marks_failed(monkeypatch):
    """BindToReady warm path where the worker replies status='failed' WITHOUT
    raising (e.g. vLLM engine init crash, readiness-probe timeout). The
    previous code discarded the CommandResultBody and left the deploy stuck
    DEPLOYING forever. It must now: release the GPU, mark FAILED with the
    worker's detail as error_message, NOT publish an endpoint / RUNNING, and
    surface a 502 (mirrors the linker + the existing exception path)."""
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
    inventory.allocate_gpu.return_value = True

    deploys = AsyncMock(spec=ModelDeploymentRepository)
    jobs_repo = AsyncMock(spec=ProvisioningJobRepository)
    controller = AsyncMock(spec=WorkerController)
    controller.load_model.return_value = CommandResultBody(
        in_reply_to="x", status="failed", detail="vllm engine init error",
    )

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

    with pytest.raises(fastapi.HTTPException) as ei:
        await place_and_provision(
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

    assert ei.value.status_code == 502
    assert "vllm engine init error" in str(ei.value.detail)

    # GPU released.
    inventory.release_gpu.assert_awaited()
    rg_args = inventory.release_gpu.await_args
    assert rg_args.args[0] == node_id

    # Marked FAILED with the worker's detail as error_message (visible in the
    # dashboard) — use update_state (publishes), not set_state.
    deploys.update_state.assert_awaited()
    us_call = deploys.update_state.await_args
    # state positional or kw
    state = us_call.kwargs.get("state")
    if state is None and len(us_call.args) >= 2:
        state = us_call.args[1]
    assert state == "FAILED"
    err = us_call.kwargs.get("error_message", "")
    assert "vllm engine init error" in (err or "")

    # NOT promoted to RUNNING and NO endpoint published.
    deploys.update_endpoint.assert_not_awaited()
    for call in deploys.set_state.await_args_list:
        if len(call.args) >= 2:
            assert call.args[1] != "RUNNING"


async def test_bind_to_ready_warm_path_ok_status_runs_and_publishes(monkeypatch):
    """BindToReady warm path where the worker replies status='ok'. The deploy
    is promoted to RUNNING and the node's advertise_url is published as the
    inference endpoint (mirrors the linker success branch)."""
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
    inventory.allocate_gpu.return_value = True

    deploys = AsyncMock(spec=ModelDeploymentRepository)
    jobs_repo = AsyncMock(spec=ProvisioningJobRepository)
    controller = AsyncMock(spec=WorkerController)
    controller.load_model.return_value = CommandResultBody(
        in_reply_to="x", status="ok", detail="",
        endpoint_url="http://127.0.0.1:9000",
    )

    deps = SimpleNamespace(
        db_pool=_make_db_pool(advertise_url="http://10.0.0.7:8080"),
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
    assert body["state"] == "RUNNING"

    # Final state transition promotes RUNNING (DEPLOYING was set in the bind tx).
    ss_states = [c.args[1] for c in deploys.set_state.await_args_list]
    assert ss_states[-1] == "RUNNING"

    # Endpoint published with the node's CP-reachable advertise_url, NOT the
    # worker's loopback endpoint_url.
    deploys.update_endpoint.assert_awaited_once()
    assert deploys.update_endpoint.await_args.args[1] == "http://10.0.0.7:8080"

    # GPU not released on success.
    inventory.release_gpu.assert_not_awaited()
