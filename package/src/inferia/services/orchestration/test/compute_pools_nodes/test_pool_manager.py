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
        request.region_constraint = []
        request.use_spot = False
        request.gpu_count = 1

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
        request.region_constraint = []
        request.use_spot = False
        request.gpu_count = 1

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
        pool_service.repo.get = AsyncMock(
            return_value={"id": pool_id, "lifecycle_state": "terminated"}
        )
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
    async def test_delete_aws_pool_routes_through_force_cancel_pool(
        self, pool_service,
    ):
        """When an AWS pool is deleted, teardown must route through the
        reconciler via ``force_cancel_pool(pool_id)`` (per-node
        ``inferia-<node_id>`` stacks) — NOT the leaky pool-scoped
        ``aws_deprovision._spawn_destroy`` path."""
        from inferia.services.orchestration.services.adapter_engine import (
            aws_deprovision,
        )
        from inferia.services.orchestration.services.provisioning.jobs import (
            repository as jobs_repository,
        )
        from unittest.mock import patch

        pool_id = uuid4()
        pool_service.repo.get = AsyncMock(
            return_value={
                "id": pool_id,
                "lifecycle_state": "terminated",
                "provider": "aws",
            },
        )
        pool_service.deployment_repo.list = AsyncMock(return_value=[])
        pool_service.repo.db = MagicMock()  # any truthy db_pool

        # Fake ProvisioningJobRepository capturing force_cancel_pool args.
        force_cancel_pool_calls: list[dict] = []

        class FakeJobsRepo:
            def __init__(self, db):
                self.db = db

            async def force_cancel_pool(self, *, pool_id):
                force_cancel_pool_calls.append({"pool_id": pool_id})
                return 2  # number of node jobs flipped

        with patch.object(
            jobs_repository, "ProvisioningJobRepository", FakeJobsRepo,
        ), patch.object(aws_deprovision, "_spawn_destroy") as spawn:
            request = MagicMock()
            request.pool_id = str(pool_id)
            ctx = make_mock_context()
            await pool_service.DeletePool(request, ctx)

        # The reconciler force_cancel_pool was used (node-scoped teardown).
        assert force_cancel_pool_calls == [{"pool_id": pool_id}]
        # The leaky pool-scoped destroy was NOT used.
        spawn.assert_not_called()
        # Pool row soft-deleted after teardown was queued.
        pool_service.repo.soft_delete_pool.assert_called_once_with(pool_id)

    @pytest.mark.asyncio
    async def test_delete_aws_pool_soft_deletes_even_if_force_cancel_fails(
        self, pool_service,
    ):
        """A force_cancel_pool error must not block the pool soft-delete;
        the reconciler/orphan-sweep is the backstop."""
        from inferia.services.orchestration.services.provisioning.jobs import (
            repository as jobs_repository,
        )
        from unittest.mock import patch

        pool_id = uuid4()
        pool_service.repo.get = AsyncMock(
            return_value={
                "id": pool_id,
                "lifecycle_state": "terminated",
                "provider": "aws",
            },
        )
        pool_service.deployment_repo.list = AsyncMock(return_value=[])
        pool_service.repo.db = MagicMock()

        class BoomJobsRepo:
            def __init__(self, db):
                self.db = db

            async def force_cancel_pool(self, *, pool_id):
                raise RuntimeError("queue down")

        with patch.object(
            jobs_repository, "ProvisioningJobRepository", BoomJobsRepo,
        ):
            request = MagicMock()
            request.pool_id = str(pool_id)
            ctx = make_mock_context()
            await pool_service.DeletePool(request, ctx)

        pool_service.repo.soft_delete_pool.assert_called_once_with(pool_id)

    @pytest.mark.asyncio
    async def test_delete_non_aws_pool_keeps_legacy_path(self, pool_service):
        """Non-AWS pools (worker/nosana/akash) skip EC2 teardown entirely —
        neither the reconciler force_cancel_pool nor the legacy destroy run."""
        from inferia.services.orchestration.services.adapter_engine import (
            aws_deprovision,
        )
        from inferia.services.orchestration.services.provisioning.jobs import (
            repository as jobs_repository,
        )
        from unittest.mock import patch

        pool_id = uuid4()
        pool_service.repo.get = AsyncMock(
            return_value={
                "id": pool_id,
                "lifecycle_state": "terminated",
                "provider": "on_prem",
            },
        )
        pool_service.deployment_repo.list = AsyncMock(return_value=[])
        pool_service.repo.db = MagicMock()

        with patch.object(aws_deprovision, "_spawn_destroy") as spawn, patch.object(
            jobs_repository, "ProvisioningJobRepository",
        ) as jobs_repo_cls:
            request = MagicMock()
            request.pool_id = str(pool_id)
            ctx = make_mock_context()
            await pool_service.DeletePool(request, ctx)

        spawn.assert_not_called()
        jobs_repo_cls.assert_not_called()
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
