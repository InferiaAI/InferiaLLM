from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from inferia.services.orchestration.services.model_deployment.controller import (
    ModelDeploymentController,
)


@pytest.fixture
def controller_and_mocks():
    deployments = SimpleNamespace(
        get=AsyncMock(),
        update_state=AsyncMock(),
    )
    event_bus = SimpleNamespace(
        publish=AsyncMock(),
    )
    controller = ModelDeploymentController(
        model_registry_repo=SimpleNamespace(),
        deployment_repo=deployments,
        outbox_repo=SimpleNamespace(),
        event_bus=event_bus,
    )
    return controller, deployments, event_bus


@pytest.mark.asyncio
async def test_start_external_deployment_sets_running_and_skips_worker_event(
    controller_and_mocks,
):
    controller, deployments, event_bus = controller_and_mocks
    deployment_id = uuid4()
    pool_id = uuid4()

    deployments.get.return_value = {
        "deployment_id": deployment_id,
        "state": "STOPPED",
        "configuration": {"workload_type": "external"},
        "pool_id": pool_id,
        "replicas": 1,
        "gpu_per_replica": 0,
        "model_id": None,
        "engine": "openai",
        "owner_id": "user-1",
    }

    next_state = await controller.start_deployment(deployment_id)

    assert next_state == "RUNNING"
    deployments.update_state.assert_awaited_once_with(deployment_id, "RUNNING")
    event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_compute_deployment_queues_worker_event_with_workload_type(
    controller_and_mocks,
):
    controller, deployments, event_bus = controller_and_mocks
    deployment_id = uuid4()
    pool_id = uuid4()

    deployments.get.return_value = {
        "deployment_id": deployment_id,
        "state": "STOPPED",
        "configuration": {"workload_type": "training"},
        "pool_id": pool_id,
        "replicas": 1,
        "gpu_per_replica": 1,
        "model_id": None,
        "engine": "vllm",
        "owner_id": "user-1",
    }

    next_state = await controller.start_deployment(deployment_id)

    assert next_state == "PENDING"
    deployments.update_state.assert_awaited_once_with(deployment_id, "PENDING")
    event_bus.publish.assert_awaited_once()
    event_name, payload = event_bus.publish.await_args.args
    assert event_name == "model.deploy.requested"
    assert payload["workload_type"] == "training"
    assert payload["pool_id"] == str(pool_id)


@pytest.mark.asyncio
async def test_start_deployment_rejects_invalid_state(controller_and_mocks):
    controller, deployments, event_bus = controller_and_mocks
    deployment_id = uuid4()

    deployments.get.return_value = {
        "deployment_id": deployment_id,
        "state": "RUNNING",
        "configuration": {"workload_type": "external"},
        "pool_id": uuid4(),
        "replicas": 1,
        "gpu_per_replica": 0,
        "model_id": None,
    }

    with pytest.raises(ValueError, match="Cannot start deployment in state"):
        await controller.start_deployment(deployment_id)

    deployments.update_state.assert_not_awaited()
    event_bus.publish.assert_not_awaited()
