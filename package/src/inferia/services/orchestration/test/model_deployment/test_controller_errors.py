"""Tests for model deployment controller — error handling layer."""

import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager

from inferia.services.orchestration.services.model_deployment.controller import (
    ModelDeploymentController,
)


@asynccontextmanager
async def mock_transaction():
    yield MagicMock()


@pytest.fixture
def controller():
    """Create controller with mocked repos."""
    deployment_repo = AsyncMock()
    deployment_repo.transaction = mock_transaction

    return ModelDeploymentController(
        model_registry_repo=AsyncMock(),
        deployment_repo=deployment_repo,
        outbox_repo=AsyncMock(),
        event_bus=AsyncMock(),
        pool_repo=AsyncMock(),
    )


class TestControllerErrors:
    """Verify controller error handling."""

    @pytest.mark.asyncio
    async def test_deploy_pool_not_found(self, controller):
        controller.pool_repo.get = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="not found"):
            await controller.deploy_model(
                model_name="test",
                model_version="1.0",
                pool_id=uuid4(),
                replicas=1,
                gpu_per_replica=1,
                workload_type="inference",
            )

    @pytest.mark.asyncio
    async def test_deploy_pool_inactive(self, controller):
        controller.pool_repo.get = AsyncMock(return_value={"is_active": False})
        with pytest.raises(ValueError, match="not active"):
            await controller.deploy_model(
                model_name="test",
                model_version="1.0",
                pool_id=uuid4(),
                replicas=1,
                gpu_per_replica=1,
                workload_type="inference",
            )

    @pytest.mark.asyncio
    async def test_delete_nonexistent_deployment(self, controller):
        controller.deployments.get = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="not found"):
            await controller.request_delete(uuid4())

    @pytest.mark.asyncio
    async def test_delete_already_terminated_is_noop(self, controller):
        controller.deployments.get = AsyncMock(return_value={"state": "TERMINATED"})
        await controller.request_delete(uuid4())
        controller.deployments.update_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_terminating_is_noop(self, controller):
        controller.deployments.get = AsyncMock(return_value={"state": "TERMINATING"})
        await controller.request_delete(uuid4())
        controller.deployments.update_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_nonexistent_deployment(self, controller):
        controller.deployments.get = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="not found"):
            await controller.start_deployment(uuid4())

    @pytest.mark.asyncio
    async def test_start_deployment_invalid_state(self, controller):
        controller.deployments.get = AsyncMock(
            return_value={"state": "RUNNING", "configuration": "{}"}
        )
        with pytest.raises(ValueError, match="Cannot start"):
            await controller.start_deployment(uuid4())

    @pytest.mark.asyncio
    async def test_external_deploy_state_is_running(self, controller):
        """External deployments go straight to RUNNING state."""
        result = await controller.deploy_model(
            model_name="gpt-4",
            model_version="1.0",
            pool_id=uuid4(),
            replicas=1,
            gpu_per_replica=0,
            workload_type="external",
            engine="openai",
        )

        # Verify deployment was created
        assert result is not None
        create_kwargs = controller.deployments.create.call_args.kwargs
        assert create_kwargs["state"] == "RUNNING"

        # External deploys should NOT trigger event_bus.publish
        controller.event_bus.publish.assert_not_called()
