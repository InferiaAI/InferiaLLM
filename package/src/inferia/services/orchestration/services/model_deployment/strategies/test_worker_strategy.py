"""Tests for WorkerDeploymentStrategy."""

from __future__ import annotations

import pytest

from inferia.services.orchestration.services.model_deployment.strategies.worker import (
    WorkerDeploymentStrategy,
)
from inferia.services.orchestration.services.worker_controller.protocol import (
    CommandResultBody,
)


class FakeScheduler:
    def __init__(self, allocate_ok: bool = True, allocate_reason: str = ""):
        self.allocate_ok = allocate_ok
        self.allocate_reason = allocate_reason
        self.allocate_calls: list = []
        self.release_calls: list = []

    async def allocate(self, **kw):
        self.allocate_calls.append(kw)
        return (self.allocate_ok, self.allocate_reason, None)

    async def release(self, **kw):
        self.release_calls.append(kw)


class FakeWorkerController:
    def __init__(self, result=None, raises=False):
        self.result = result or CommandResultBody(
            in_reply_to="x", status="ok",
            endpoint_url="http://worker:8080",
        )
        self.raises = raises
        self.calls: list = []

    async def load_model(self, *, node_id, spec):
        self.calls.append((node_id, spec))
        if self.raises:
            raise RuntimeError("controller boom")
        return self.result


def _model():
    return {
        "artifact_uri": "hf://org/m",
        "backend": "vllm",
        "config": {"dtype": "bfloat16"},
    }


@pytest.mark.asyncio
async def test_deploy_happy_path():
    sched = FakeScheduler()
    ctrl = FakeWorkerController()
    s = WorkerDeploymentStrategy(scheduler_repo=sched, worker_controller=ctrl)
    result = await s.deploy(
        deployment_id="dep-1", model=_model(),
        pool_id="p", node_id="node-a", replicas=1,
        gpu_per_replica=1, vcpu_per_replica=2, ram_gb_per_replica=8,
        workload_type="llm",
    )
    assert result["runtime"] == "worker"
    assert result["endpoint"] == "http://worker:8080"
    assert len(sched.allocate_calls) == 1
    assert sched.allocate_calls[0]["node_id"] == "node-a"
    assert sched.allocate_calls[0]["gpu"] == 1


@pytest.mark.asyncio
async def test_deploy_rejects_multireplica():
    s = WorkerDeploymentStrategy(
        scheduler_repo=FakeScheduler(), worker_controller=FakeWorkerController(),
    )
    with pytest.raises(ValueError, match="single-replica"):
        await s.deploy(
            deployment_id="d", model=_model(),
            pool_id="p", node_id="n", replicas=2,
            gpu_per_replica=1, vcpu_per_replica=2, ram_gb_per_replica=8,
            workload_type="llm",
        )


@pytest.mark.asyncio
async def test_deploy_releases_on_load_failure():
    sched = FakeScheduler()
    ctrl = FakeWorkerController(result=CommandResultBody(
        in_reply_to="x", status="failed", detail="oom",
    ))
    s = WorkerDeploymentStrategy(scheduler_repo=sched, worker_controller=ctrl)
    with pytest.raises(RuntimeError, match="oom"):
        await s.deploy(
            deployment_id="d", model=_model(),
            pool_id="p", node_id="n", replicas=1,
            gpu_per_replica=1, vcpu_per_replica=2, ram_gb_per_replica=8,
            workload_type="llm",
        )
    assert len(sched.release_calls) == 1


@pytest.mark.asyncio
async def test_deploy_releases_on_controller_exception():
    sched = FakeScheduler()
    ctrl = FakeWorkerController(raises=True)
    s = WorkerDeploymentStrategy(scheduler_repo=sched, worker_controller=ctrl)
    with pytest.raises(RuntimeError):
        await s.deploy(
            deployment_id="d", model=_model(),
            pool_id="p", node_id="n", replicas=1,
            gpu_per_replica=1, vcpu_per_replica=2, ram_gb_per_replica=8,
            workload_type="llm",
        )
    assert len(sched.release_calls) == 1


@pytest.mark.asyncio
async def test_deploy_rejects_allocation_failure():
    sched = FakeScheduler(allocate_ok=False, allocate_reason="quota_exceeded")
    ctrl = FakeWorkerController()
    s = WorkerDeploymentStrategy(scheduler_repo=sched, worker_controller=ctrl)
    with pytest.raises(RuntimeError, match="quota_exceeded"):
        await s.deploy(
            deployment_id="d", model=_model(),
            pool_id="p", node_id="n", replicas=1,
            gpu_per_replica=1, vcpu_per_replica=2, ram_gb_per_replica=8,
            workload_type="llm",
        )
    # No controller call — allocation failed first.
    assert ctrl.calls == []
    # No release either — never allocated.
    assert sched.release_calls == []


@pytest.mark.asyncio
async def test_deploy_uses_backend_as_recipe():
    sched = FakeScheduler()
    ctrl = FakeWorkerController()
    s = WorkerDeploymentStrategy(scheduler_repo=sched, worker_controller=ctrl)
    model = _model()
    model["backend"] = "ollama"
    await s.deploy(
        deployment_id="d", model=model,
        pool_id="p", node_id="n", replicas=1,
        gpu_per_replica=1, vcpu_per_replica=2, ram_gb_per_replica=8,
        workload_type="llm",
    )
    assert ctrl.calls[0][1]["recipe"] == "ollama"


@pytest.mark.asyncio
async def test_deploy_gpu_indices_match_per_replica():
    sched = FakeScheduler()
    ctrl = FakeWorkerController()
    s = WorkerDeploymentStrategy(scheduler_repo=sched, worker_controller=ctrl)
    await s.deploy(
        deployment_id="d", model=_model(),
        pool_id="p", node_id="n", replicas=1,
        gpu_per_replica=2, vcpu_per_replica=4, ram_gb_per_replica=16,
        workload_type="llm",
    )
    assert ctrl.calls[0][1]["gpu_indices"] == [0, 1]


@pytest.mark.asyncio
async def test_release_swallows_errors():
    """If scheduler.release itself raises, the original exception still
    propagates and the test should not see a different one."""

    class FailingRelease(FakeScheduler):
        async def release(self, **kw):
            raise RuntimeError("release boom")

    sched = FailingRelease()
    ctrl = FakeWorkerController(result=CommandResultBody(
        in_reply_to="x", status="failed", detail="primary",
    ))
    s = WorkerDeploymentStrategy(scheduler_repo=sched, worker_controller=ctrl)
    with pytest.raises(RuntimeError, match="primary"):
        await s.deploy(
            deployment_id="d", model=_model(),
            pool_id="p", node_id="n", replicas=1,
            gpu_per_replica=1, vcpu_per_replica=2, ram_gb_per_replica=8,
            workload_type="llm",
        )
