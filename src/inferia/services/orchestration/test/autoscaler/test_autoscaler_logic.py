"""Tests for autoscaler logic — complex logic layer."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import AsyncMock

from inferia.services.orchestration.services.autoscaler.worker import Autoscaler


def make_pool(max_nodes=5, min_nodes=1, scale_up=80, scale_down=20, cooldown=60):
    return {
        "id": "pool-1",
        "provider": "test-provider",
        "autoscaling_policy": json.dumps(
            {
                "max_nodes": max_nodes,
                "min_nodes": min_nodes,
                "scale_up_threshold": scale_up,
                "scale_down_threshold": scale_down,
                "cooldown_seconds": cooldown,
            }
        ),
    }


@pytest.fixture
def autoscaler():
    repo = AsyncMock()
    adapter = AsyncMock()
    return Autoscaler(repo=repo, adapter_engine=adapter)


class TestAutoscalerLogic:
    """Verify autoscaler scaling decisions."""

    @pytest.mark.asyncio
    async def test_scale_up_on_high_utilization(self, autoscaler):
        autoscaler.repo.get_pools = AsyncMock(return_value=[make_pool()])
        autoscaler.repo.pool_stats = AsyncMock(
            return_value={"ready_nodes": 2, "avg_cpu_util": 90, "idle_nodes": 0}
        )
        autoscaler.repo.state = AsyncMock(
            return_value={"last_scale_at": None, "consecutive_failures": 0}
        )

        await autoscaler.tick()
        autoscaler.adapter.provision_node.assert_called_once()

    @pytest.mark.asyncio
    async def test_scale_down_on_low_utilization(self, autoscaler):
        autoscaler.repo.get_pools = AsyncMock(
            return_value=[make_pool(min_nodes=1)]
        )
        autoscaler.repo.pool_stats = AsyncMock(
            return_value={"ready_nodes": 3, "avg_cpu_util": 10, "idle_nodes": 1}
        )
        autoscaler.repo.state = AsyncMock(
            return_value={"last_scale_at": None, "consecutive_failures": 0}
        )
        autoscaler.repo.find_idle_node = AsyncMock(
            return_value={
                "id": "node-1",
                "provider": "test",
                "provider_instance_id": "inst-1",
            }
        )

        await autoscaler.tick()
        autoscaler.adapter.deprovision_node.assert_called_once()

    @pytest.mark.asyncio
    async def test_cooldown_prevents_scaling(self, autoscaler):
        """No scaling action during cooldown period."""
        autoscaler.repo.get_pools = AsyncMock(
            return_value=[make_pool(cooldown=300)]
        )
        autoscaler.repo.pool_stats = AsyncMock(
            return_value={"ready_nodes": 2, "avg_cpu_util": 90, "idle_nodes": 0}
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        autoscaler.repo.state = AsyncMock(
            return_value={
                "last_scale_at": now - timedelta(seconds=10),
                "consecutive_failures": 0,
            }
        )

        await autoscaler.tick()
        autoscaler.adapter.provision_node.assert_not_called()

    @pytest.mark.asyncio
    async def test_max_nodes_prevents_scale_up(self, autoscaler):
        autoscaler.repo.get_pools = AsyncMock(
            return_value=[make_pool(max_nodes=3)]
        )
        autoscaler.repo.pool_stats = AsyncMock(
            return_value={"ready_nodes": 3, "avg_cpu_util": 90, "idle_nodes": 0}
        )
        autoscaler.repo.state = AsyncMock(
            return_value={"last_scale_at": None, "consecutive_failures": 0}
        )

        await autoscaler.tick()
        autoscaler.adapter.provision_node.assert_not_called()

    @pytest.mark.asyncio
    async def test_min_nodes_prevents_scale_down(self, autoscaler):
        autoscaler.repo.get_pools = AsyncMock(
            return_value=[make_pool(min_nodes=2)]
        )
        autoscaler.repo.pool_stats = AsyncMock(
            return_value={"ready_nodes": 2, "avg_cpu_util": 10, "idle_nodes": 1}
        )
        autoscaler.repo.state = AsyncMock(
            return_value={"last_scale_at": None, "consecutive_failures": 0}
        )

        await autoscaler.tick()
        autoscaler.adapter.deprovision_node.assert_not_called()
