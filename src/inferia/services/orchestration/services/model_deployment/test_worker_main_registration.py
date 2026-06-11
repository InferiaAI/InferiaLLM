"""Smoke test for the worker_main strategy registration.

The actual ``main()`` coroutine in worker_main connects to Postgres and
Redis. We can't exercise it end-to-end in a unit-test sandbox, but we can
verify the imports it depends on are wired correctly and the strategy
class is constructible with the same DI shape main() uses.
"""

import pytest


def test_worker_main_has_strategy_registration():
    """worker_main.py registers 'worker' in runtime_strategies and imports
    WorkerDeploymentStrategy + WorkerRegistry + WorkerController.

    The full module import requires k8s/grpc/asyncpg/redis at runtime, so
    we verify the wiring by source inspection rather than import.
    """
    import pathlib
    src = pathlib.Path(
        "package/src/inferia/services/orchestration/services/model_deployment/worker_main.py"
    ).read_text()

    assert "from inferia.services.orchestration.services.model_deployment.strategies.worker import WorkerDeploymentStrategy" in src, (
        "WorkerDeploymentStrategy not imported in worker_main.py"
    )
    assert "WorkerDeploymentStrategy(" in src, (
        "WorkerDeploymentStrategy not constructed in worker_main.py"
    )
    assert '"worker": worker_strategy' in src or "'worker': worker_strategy" in src, (
        "'worker' key not registered in runtime_strategies"
    )


def test_strategy_constructible_with_main_di_shape():
    from inferia.services.orchestration.services.model_deployment.strategies.worker import (
        WorkerDeploymentStrategy,
    )
    from inferia.services.orchestration.services.worker_controller.controller import (
        WorkerController,
    )
    from inferia.services.orchestration.services.worker_controller.registry import (
        WorkerRegistry,
    )

    class FakeScheduler:
        pass

    s = WorkerDeploymentStrategy(
        scheduler_repo=FakeScheduler(),
        worker_controller=WorkerController(WorkerRegistry()),
    )
    assert hasattr(s, "deploy")


def test_strategy_signature_matches_other_strategies():
    """All three strategies registered in worker_main must accept the same
    keyword arguments to ``deploy()`` so the dispatcher can call them
    uniformly."""
    import inspect
    from inferia.services.orchestration.services.model_deployment.strategies.vllm import (
        VLLMDeploymentStrategy,
    )
    from inferia.services.orchestration.services.model_deployment.strategies.localai import (
        LocalAIDeploymentStrategy,
    )
    from inferia.services.orchestration.services.model_deployment.strategies.worker import (
        WorkerDeploymentStrategy,
    )

    keys = []
    for cls in (VLLMDeploymentStrategy, LocalAIDeploymentStrategy, WorkerDeploymentStrategy):
        sig = inspect.signature(cls.deploy)
        keys.append(set(sig.parameters.keys()))

    # All three should share the same kwarg set (modulo self).
    base = keys[0]
    for k in keys[1:]:
        assert k == base, f"strategy signature drift: {base} vs {k}"
