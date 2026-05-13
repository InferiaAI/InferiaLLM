"""Tests for WorkerDeploymentStrategy."""

import pytest

from inferia.services.orchestration.services.model_deployment.strategies.worker import (
    WorkerDeploymentStrategy,
)
from inferia.services.orchestration.services.worker_controller.protocol import (
    CommandResultBody,
)


class FakePlacement:
    def __init__(self, node_ids):
        self.node_ids = list(node_ids)
        self.calls = 0

    async def place_workload(self, **_kw):
        idx = self.calls
        self.calls += 1
        if idx >= len(self.node_ids):
            raise RuntimeError("ran out of nodes in fake placement")

        class _P:
            pass
        p = _P()
        p.node_id = self.node_ids[idx]
        return p


class FakeScheduler:
    def __init__(self):
        self.calls = []

    async def allocate(self, **kw):
        self.calls.append(kw)

        class _A:
            pass
        a = _A()
        a.allocation_id = f"alloc-{len(self.calls)}"
        return a


class FakeWorkerController:
    def __init__(self, results=None, raise_for=None):
        self.results = results or {}
        self.raise_for = raise_for or set()
        self.load_calls = []

    async def load_model(self, *, node_id, spec):
        self.load_calls.append((node_id, spec))
        if node_id in self.raise_for:
            raise RuntimeError(f"fake controller raise for {node_id}")
        return self.results.get(node_id, CommandResultBody(
            in_reply_to="x", status="ok", endpoint_url=f"http://{node_id}:8080",
        ))


def _model():
    return {
        "artifact_uri": "hf://org/m",
        "backend": "vllm",
        "config": {"dtype": "bfloat16"},
    }


@pytest.mark.asyncio
async def test_deploy_single_replica_happy_path():
    placement = FakePlacement(node_ids=["node-a"])
    scheduler = FakeScheduler()
    ctrl = FakeWorkerController()
    s = WorkerDeploymentStrategy(placement, scheduler, ctrl)

    result = await s.deploy(
        deployment_id="dep-1", model=_model(),
        pool_id="p", replicas=1, gpu_per_replica=1, workload_type="llm",
    )
    assert result["runtime"] == "worker"
    assert result["endpoint"] == "http://node-a:8080"
    assert len(ctrl.load_calls) == 1
    assert len(scheduler.calls) == 1
    assert ctrl.load_calls[0][1]["recipe"] == "vllm"
    assert ctrl.load_calls[0][1]["model"]["artifact_uri"] == "hf://org/m"


@pytest.mark.asyncio
async def test_deploy_multi_replica_records_each_endpoint():
    placement = FakePlacement(node_ids=["node-a", "node-b", "node-c"])
    scheduler = FakeScheduler()
    ctrl = FakeWorkerController()
    s = WorkerDeploymentStrategy(placement, scheduler, ctrl)

    result = await s.deploy(
        deployment_id="dep-2", model=_model(),
        pool_id="p", replicas=3, gpu_per_replica=1, workload_type="llm",
    )
    assert len(result["endpoints"]) == 3
    assert result["endpoint"] == result["endpoints"][0]
    assert len(ctrl.load_calls) == 3


@pytest.mark.asyncio
async def test_deploy_failed_command_result_raises():
    placement = FakePlacement(node_ids=["node-a"])
    scheduler = FakeScheduler()
    ctrl = FakeWorkerController(results={
        "node-a": CommandResultBody(in_reply_to="x", status="failed", detail="oom"),
    })
    s = WorkerDeploymentStrategy(placement, scheduler, ctrl)
    with pytest.raises(RuntimeError, match="oom"):
        await s.deploy(
            deployment_id="d", model=_model(),
            pool_id="p", replicas=1, gpu_per_replica=1, workload_type="llm",
        )


@pytest.mark.asyncio
async def test_deploy_controller_exception_bubbles():
    placement = FakePlacement(node_ids=["node-a"])
    scheduler = FakeScheduler()
    ctrl = FakeWorkerController(raise_for={"node-a"})
    s = WorkerDeploymentStrategy(placement, scheduler, ctrl)
    with pytest.raises(RuntimeError):
        await s.deploy(
            deployment_id="d", model=_model(),
            pool_id="p", replicas=1, gpu_per_replica=1, workload_type="llm",
        )


@pytest.mark.asyncio
async def test_deploy_uses_backend_field_as_recipe():
    placement = FakePlacement(node_ids=["node-a"])
    ctrl = FakeWorkerController()
    s = WorkerDeploymentStrategy(placement, FakeScheduler(), ctrl)
    model = _model()
    model["backend"] = "ollama"
    await s.deploy(
        deployment_id="d", model=model,
        pool_id="p", replicas=1, gpu_per_replica=1, workload_type="llm",
    )
    assert ctrl.load_calls[0][1]["recipe"] == "ollama"


@pytest.mark.asyncio
async def test_deploy_passes_gpu_indices_matching_per_replica():
    placement = FakePlacement(node_ids=["node-a"])
    ctrl = FakeWorkerController()
    s = WorkerDeploymentStrategy(placement, FakeScheduler(), ctrl)
    await s.deploy(
        deployment_id="d", model=_model(),
        pool_id="p", replicas=1, gpu_per_replica=2, workload_type="llm",
    )
    assert ctrl.load_calls[0][1]["gpu_indices"] == [0, 1]


@pytest.mark.asyncio
async def test_deploy_records_allocation_ids():
    placement = FakePlacement(node_ids=["a", "b"])
    sched = FakeScheduler()
    s = WorkerDeploymentStrategy(placement, sched, FakeWorkerController())
    result = await s.deploy(
        deployment_id="d", model=_model(),
        pool_id="p", replicas=2, gpu_per_replica=1, workload_type="llm",
    )
    assert result["allocations"] == ["alloc-1", "alloc-2"]
