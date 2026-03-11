"""Tests for compute pool manager — complex logic layer."""

import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta, timezone

from inferia.services.orchestration.services.compute_pool_engine.compute_pool_manager import (
    ComputePoolManagerService,
)


def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def make_mock_context():
    """Create a mock gRPC context."""
    ctx = MagicMock()

    def abort_fn(code, message):
        raise Exception(f"gRPC abort: {message}")

    ctx.abort = MagicMock(side_effect=abort_fn)
    return ctx


@pytest.fixture
def pool_service():
    repo = AsyncMock()
    deployment_repo = AsyncMock()
    controller = AsyncMock()
    return ComputePoolManagerService(
        repo=repo, deployment_repo=deployment_repo, controller=controller
    )


class TestPoolManager:
    """Verify compute pool manager operations."""

    @pytest.mark.asyncio
    async def test_register_pool_creates_pool(self, pool_service):
        pool_id = uuid4()
        pool_service.repo.create_pool = AsyncMock(return_value=pool_id)

        request = MagicMock()
        request.pool_name = "test-pool"
        request.owner_type = "user"
        request.owner_id = "user-1"
        request.provider = "nosana"
        request.allowed_gpu_types = ["a100"]
        request.max_cost_per_hour = 10.0
        request.is_dedicated = False
        request.provider_pool_id = "pool-ext"
        request.scheduling_policy_json = ""
        request.provider_credential_name = ""

        ctx = make_mock_context()
        response = await pool_service.RegisterPool(request, ctx)
        pool_service.repo.create_pool.assert_called_once()
        assert response.pool_name == "test-pool"

    @pytest.mark.asyncio
    async def test_register_pool_invalid_credential_aborts(self, pool_service):
        pool_service.repo.credential_exists = AsyncMock(return_value=False)

        request = MagicMock()
        request.pool_name = "test-pool"
        request.owner_type = "user"
        request.owner_id = "user-1"
        request.provider = "nosana"
        request.allowed_gpu_types = ["a100"]
        request.max_cost_per_hour = 10.0
        request.is_dedicated = False
        request.provider_pool_id = "pool-ext"
        request.scheduling_policy_json = ""
        request.provider_credential_name = "bad-cred"

        ctx = make_mock_context()
        with pytest.raises(Exception, match="gRPC abort"):
            await pool_service.RegisterPool(request, ctx)

    @pytest.mark.asyncio
    async def test_get_pool_not_found_aborts(self, pool_service):
        pool_service.repo.get = AsyncMock(return_value=None)

        request = MagicMock()
        request.pool_id = str(uuid4())

        ctx = make_mock_context()
        with pytest.raises(Exception, match="gRPC abort"):
            await pool_service.GetPool(request, ctx)

    @pytest.mark.asyncio
    async def test_delete_pool_with_deployments_cascades(self, pool_service):
        """Deleting pool triggers cascade cleanup of deployments."""
        pool_id = uuid4()
        pool_service.deployment_repo.list = AsyncMock(
            return_value=[
                {"deployment_id": uuid4(), "state": "STOPPED"},
                {"deployment_id": uuid4(), "state": "RUNNING"},
            ]
        )

        request = MagicMock()
        request.pool_id = str(pool_id)
        ctx = make_mock_context()

        await pool_service.DeletePool(request, ctx)

        # Stopped deployment should be deleted directly
        pool_service.deployment_repo.delete.assert_called_once()
        # Running deployment should request termination
        pool_service.controller.request_delete.assert_called_once()
        # Pool itself soft-deleted
        pool_service.repo.soft_delete_pool.assert_called_once_with(pool_id)

    @pytest.mark.asyncio
    async def test_list_inventory_filters_stale_nodes(self, pool_service):
        """Nodes with heartbeat older than 2 minutes are excluded."""
        now = utcnow_naive()
        fresh_node = {
            "node_id": str(uuid4()),
            "provider": "nosana",
            "state": "ready",
            "gpu_total": 1,
            "gpu_allocated": 0,
            "vcpu_total": 8,
            "vcpu_allocated": 0,
            "expose_url": "http://host:8000",
            "last_heartbeat": now - timedelta(seconds=30),
            "created_at": now - timedelta(hours=1),
        }
        stale_node = {
            "node_id": str(uuid4()),
            "provider": "nosana",
            "state": "ready",
            "gpu_total": 1,
            "gpu_allocated": 0,
            "vcpu_total": 8,
            "vcpu_allocated": 0,
            "expose_url": "http://stale:8000",
            "last_heartbeat": now - timedelta(minutes=5),
            "created_at": now - timedelta(hours=1),
        }
        terminated_node = {
            "node_id": str(uuid4()),
            "provider": "nosana",
            "state": "terminated",
            "gpu_total": 1,
            "gpu_allocated": 0,
            "vcpu_total": 8,
            "vcpu_allocated": 0,
            "expose_url": "",
            "last_heartbeat": now,
            "created_at": now - timedelta(hours=1),
        }

        pool_service.repo.list_pool_inventory = AsyncMock(
            return_value=[fresh_node, stale_node, terminated_node]
        )

        request = MagicMock()
        request.pool_id = str(uuid4())
        ctx = make_mock_context()

        response = await pool_service.ListPoolInventory(request, ctx)
        # Only fresh node should be included (stale filtered by heartbeat, terminated by state)
        assert len(response.nodes) == 1
