"""Tests that handle_terminate_requested always transitions state from TERMINATING.

Regression tests for GitHub issue #19: deployment stuck in TERMINATING state
when cleanup steps raise exceptions.
"""

import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

from orchestration.model_deployment.worker import (
    ModelDeploymentWorker,
)


@pytest.fixture
def worker():
    """Create worker with mocked repos."""
    return ModelDeploymentWorker(
        deployment_repo=AsyncMock(),
        model_registry_repo=AsyncMock(),
        pool_repo=AsyncMock(),
        placement_repo=AsyncMock(),
        scheduler=AsyncMock(),
        inventory_repo=AsyncMock(),
        runtime_resolver=MagicMock(),
        runtime_strategies={"vllm": AsyncMock()},
    )


def _make_deployment(dep_id, node_ids=None, alloc_ids=None):
    """Build a minimal deployment dict in TERMINATING state."""
    return {
        "state": "TERMINATING",
        "deployment_id": str(dep_id),
        "pool_id": str(uuid4()),
        "node_ids": node_ids or [],
        "allocation_ids": alloc_ids or [],
    }


class TestTerminateStateUpdate:
    """Ensure deployment always leaves TERMINATING state (issue #19)."""

    @pytest.mark.asyncio
    async def test_normal_termination_sets_stopped(self, worker):
        """Clean termination with no errors sets state to STOPPED."""
        dep_id = uuid4()
        node_id = str(uuid4())
        dep = _make_deployment(dep_id, node_ids=[node_id])
        worker.deployments.get = AsyncMock(return_value=dep)
        worker.pools.get = AsyncMock(return_value={"pool_type": "job"})

        node_data = {
            "provider": "nosana",
            "provider_instance_id": "inst-1",
            "metadata": {},
        }
        worker.inventory.get_node_by_id = AsyncMock(return_value=node_data)

        mock_adapter = MagicMock()
        mock_adapter.deprovision_node = AsyncMock()
        mock_adapter.get_capabilities.return_value = MagicMock(
            is_ephemeral=True, supports_cluster_mode=False
        )

        with patch(
            "orchestration.model_deployment.worker.get_adapter",
            return_value=mock_adapter,
        ):
            await worker.handle_terminate_requested(dep_id)

        worker.deployments.update_state.assert_called_with(dep_id, "STOPPED")

    @pytest.mark.asyncio
    async def test_deprovision_failure_sets_failed(self, worker):
        """If deprovision_node raises, state must transition to FAILED (not stay TERMINATING)."""
        dep_id = uuid4()
        node_id = str(uuid4())
        dep = _make_deployment(dep_id, node_ids=[node_id])
        worker.deployments.get = AsyncMock(return_value=dep)
        worker.pools.get = AsyncMock(return_value={"pool_type": "job"})

        node_data = {
            "provider": "nosana",
            "provider_instance_id": "inst-1",
            "metadata": {},
        }
        worker.inventory.get_node_by_id = AsyncMock(return_value=node_data)

        mock_adapter = MagicMock()
        mock_adapter.deprovision_node = AsyncMock(
            side_effect=RuntimeError("provider down")
        )

        with patch(
            "orchestration.model_deployment.worker.get_adapter",
            return_value=mock_adapter,
        ):
            await worker.handle_terminate_requested(dep_id)

        worker.deployments.update_state.assert_called_with(dep_id, "FAILED")

    @pytest.mark.asyncio
    async def test_scheduler_release_failure_sets_failed(self, worker):
        """If scheduler.release raises, state must transition to FAILED."""
        dep_id = uuid4()
        alloc_id = str(uuid4())
        dep = _make_deployment(dep_id, alloc_ids=[alloc_id])
        worker.deployments.get = AsyncMock(return_value=dep)
        worker.pools.get = AsyncMock(return_value={"pool_type": "job"})

        worker.scheduler.release = AsyncMock(
            side_effect=RuntimeError("redis unavailable")
        )

        with patch(
            "orchestration.model_deployment.worker.get_adapter",
            return_value=MagicMock(),
        ):
            await worker.handle_terminate_requested(dep_id)

        worker.deployments.update_state.assert_called_with(dep_id, "FAILED")
