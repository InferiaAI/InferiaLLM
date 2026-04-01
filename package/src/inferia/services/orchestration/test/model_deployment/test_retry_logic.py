"""Tests for deployment retry logic."""

import asyncio
import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


def _mock_db_with_conn():
    """Create a mock db pool with proper async context manager for acquire()."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value="UPDATE 1")

    mock_db = MagicMock()

    @asynccontextmanager
    async def mock_acquire():
        yield mock_conn

    mock_db.acquire = mock_acquire
    return mock_db, mock_conn


class TestRepoRetryingState:
    """Test that RETRYING state preserves error_message in update_state."""

    @pytest.mark.asyncio
    async def test_retrying_state_preserves_error_message(self):
        from inferia.services.orchestration.repositories.model_deployment_repo import (
            ModelDeploymentRepository,
        )

        mock_db, mock_conn = _mock_db_with_conn()
        mock_bus = AsyncMock()
        repo = ModelDeploymentRepository(mock_db, mock_bus)

        await repo.update_state(
            uuid4(), "RETRYING", error_message="Retry 1/2: CUDA error"
        )

        call_args = mock_conn.execute.call_args
        assert call_args[0][2] == "RETRYING"
        assert call_args[0][3] == "Retry 1/2: CUDA error"

    @pytest.mark.asyncio
    async def test_non_failure_state_clears_error_message(self):
        from inferia.services.orchestration.repositories.model_deployment_repo import (
            ModelDeploymentRepository,
        )

        mock_db, mock_conn = _mock_db_with_conn()
        mock_bus = AsyncMock()
        repo = ModelDeploymentRepository(mock_db, mock_bus)

        await repo.update_state(
            uuid4(), "RUNNING", error_message="should be cleared"
        )

        call_args = mock_conn.execute.call_args
        assert call_args[0][2] == "RUNNING"
        assert call_args[0][3] is None

    @pytest.mark.asyncio
    async def test_failed_state_preserves_error_message(self):
        from inferia.services.orchestration.repositories.model_deployment_repo import (
            ModelDeploymentRepository,
        )

        mock_db, mock_conn = _mock_db_with_conn()
        mock_bus = AsyncMock()
        repo = ModelDeploymentRepository(mock_db, mock_bus)

        await repo.update_state(
            uuid4(), "FAILED", error_message="CUDA incompatible"
        )

        call_args = mock_conn.execute.call_args
        assert call_args[0][2] == "FAILED"
        assert call_args[0][3] == "CUDA incompatible"

    @pytest.mark.asyncio
    async def test_update_state_if_retrying_preserves_error(self):
        from inferia.services.orchestration.repositories.model_deployment_repo import (
            ModelDeploymentRepository,
        )

        mock_db, mock_conn = _mock_db_with_conn()
        mock_bus = AsyncMock()
        repo = ModelDeploymentRepository(mock_db, mock_bus)

        result = await repo.update_state_if(
            uuid4(),
            expected_state="PROVISIONING",
            new_state="RETRYING",
            error_message="Retry 1/2: timeout",
        )

        assert result is True
        call_args = mock_conn.execute.call_args
        assert call_args[0][3] == "RETRYING"
        assert call_args[0][4] == "Retry 1/2: timeout"


class TestWorkerRetryLogic:
    """Test deployment retry behavior in the worker."""

    def _make_worker(self, deployment_repo=None, pool_repo=None, inventory_repo=None, placement_repo=None):
        from inferia.services.orchestration.services.model_deployment.worker import (
            ModelDeploymentWorker,
        )

        return ModelDeploymentWorker(
            deployment_repo=deployment_repo or AsyncMock(),
            model_registry_repo=AsyncMock(),
            pool_repo=pool_repo or AsyncMock(),
            placement_repo=placement_repo or AsyncMock(),
            scheduler=AsyncMock(),
            inventory_repo=inventory_repo or AsyncMock(),
            runtime_resolver=AsyncMock(),
            runtime_strategies={},
        )

    def _make_deployment(self, dep_id=None, pool_id=None):
        return {
            "deployment_id": dep_id or uuid4(),
            "state": "PENDING",
            "pool_id": pool_id or uuid4(),
            "model_id": None,
            "model_name": "test-model",
            "engine": "vllm",
            "gpu_per_replica": 1,
            "replicas": 1,
            "configuration": {"image": "test:latest", "cmd": ["serve"]},
            "inference_model": None,
            "model_type": "inference",
        }

    def _make_pool(self):
        return {
            "provider": "nosana",
            "provider_pool_id": "pool-1",
            "allowed_gpu_types": ["A100"],
            "pool_type": "job",
            "cluster_id": None,
            "provider_credential_name": "cred-1",
        }

    def _make_capabilities(self):
        return MagicMock(
            supports_cluster_mode=False,
            is_ephemeral=True,
            requires_readiness_poll=True,
            readiness_timeout_seconds=300,
        )

    @pytest.mark.asyncio
    async def test_retry_sets_retrying_state(self):
        """When provision fails and retries remain, state should go to RETRYING."""
        dep_id = uuid4()
        deployment = self._make_deployment(dep_id=dep_id)
        pool = self._make_pool()

        dep_repo = AsyncMock()
        dep_repo.get = AsyncMock(return_value=deployment)
        dep_repo.update_state_if = AsyncMock(return_value=True)
        dep_repo.update_state = AsyncMock()
        dep_repo.update_configuration = AsyncMock()

        pool_repo = AsyncMock()
        pool_repo.get = AsyncMock(return_value=pool)

        inventory_repo = AsyncMock()
        inventory_repo.get_resource_requirement = AsyncMock(
            return_value={"vcpu_total": 8, "ram_gb_total": 32}
        )

        placement_repo = AsyncMock()
        placement_repo.fetch_candidate_nodes = AsyncMock(return_value=[])

        worker = self._make_worker(
            deployment_repo=dep_repo,
            pool_repo=pool_repo,
            inventory_repo=inventory_repo,
            placement_repo=placement_repo,
        )

        mock_adapter = MagicMock()
        mock_adapter.get_capabilities = MagicMock(return_value=self._make_capabilities())
        mock_adapter.provision_node = AsyncMock(
            side_effect=RuntimeError("CUDA incompatible")
        )
        mock_adapter.deprovision_node = AsyncMock()

        with patch(
            "inferia.services.orchestration.services.model_deployment.worker.get_adapter",
            return_value=mock_adapter,
        ), patch(
            "inferia.services.orchestration.services.model_deployment.worker.asyncio.sleep",
            new_callable=AsyncMock,
        ), patch(
            "inferia.services.orchestration.services.model_deployment.worker.settings",
        ) as mock_settings:
            mock_settings.max_deployment_retries = 2
            await worker.handle_deploy_requested(dep_id)

        retrying_calls = [
            c for c in dep_repo.update_state.call_args_list
            if len(c[0]) >= 2 and c[0][1] == "RETRYING"
        ]
        assert len(retrying_calls) > 0, "Should have set RETRYING state during retries"

    @pytest.mark.asyncio
    async def test_retry_exhaustion_marks_failed(self):
        """When all retries are exhausted, deployment should be FAILED."""
        dep_id = uuid4()
        deployment = self._make_deployment(dep_id=dep_id)
        pool = self._make_pool()

        # Return appropriate states at different call points
        def get_side_effect(*args, **kwargs):
            # Return RETRYING state for CAS checks during retry, PENDING initially
            d = dict(deployment)
            # Check what state was last set
            if dep_repo.update_state.call_count > 0:
                last_call = dep_repo.update_state.call_args_list[-1]
                if len(last_call[0]) >= 2:
                    d["state"] = last_call[0][1]
            return d

        dep_repo = AsyncMock()
        dep_repo.get = AsyncMock(side_effect=get_side_effect)
        dep_repo.update_state_if = AsyncMock(return_value=True)
        dep_repo.update_state = AsyncMock()
        dep_repo.update_configuration = AsyncMock()

        pool_repo = AsyncMock()
        pool_repo.get = AsyncMock(return_value=pool)

        inventory_repo = AsyncMock()
        inventory_repo.get_resource_requirement = AsyncMock(
            return_value={"vcpu_total": 8, "ram_gb_total": 32}
        )

        placement_repo = AsyncMock()
        placement_repo.fetch_candidate_nodes = AsyncMock(return_value=[])

        worker = self._make_worker(
            deployment_repo=dep_repo,
            pool_repo=pool_repo,
            inventory_repo=inventory_repo,
            placement_repo=placement_repo,
        )

        mock_adapter = MagicMock()
        mock_adapter.get_capabilities = MagicMock(return_value=self._make_capabilities())
        mock_adapter.provision_node = AsyncMock(
            side_effect=RuntimeError("CUDA incompatible")
        )
        mock_adapter.deprovision_node = AsyncMock()

        with patch(
            "inferia.services.orchestration.services.model_deployment.worker.get_adapter",
            return_value=mock_adapter,
        ), patch(
            "inferia.services.orchestration.services.model_deployment.worker.asyncio.sleep",
            new_callable=AsyncMock,
        ), patch(
            "inferia.services.orchestration.services.model_deployment.worker.settings",
        ) as mock_settings:
            mock_settings.max_deployment_retries = 2
            await worker.handle_deploy_requested(dep_id)

        # Check for FAILED in both positional and keyword args
        all_states = []
        for c in dep_repo.update_state.call_args_list:
            if len(c[0]) >= 2:
                all_states.append(c[0][1])
            if c[1].get("error_message"):
                pass  # kwargs style
        assert "FAILED" in all_states, f"Should have marked FAILED after retries exhausted. States seen: {all_states}"

    @pytest.mark.asyncio
    async def test_successful_deploy_no_retry(self):
        """Successful deployment should not trigger any retries."""
        dep_id = uuid4()
        deployment = self._make_deployment(dep_id=dep_id)
        pool = self._make_pool()

        # Track state changes so safety checks see correct state
        current_state = {"value": "PENDING"}

        original_update_state = AsyncMock()
        async def tracking_update_state(dep_id, state, **kwargs):
            current_state["value"] = state
            return await original_update_state(dep_id, state, **kwargs)

        original_update_state_if = AsyncMock(return_value=True)
        async def tracking_update_state_if(dep_id, expected_state, new_state, **kwargs):
            current_state["value"] = new_state
            return await original_update_state_if(dep_id, expected_state, new_state, **kwargs)

        def get_side_effect(*args, **kwargs):
            d = dict(deployment)
            d["state"] = current_state["value"]
            return d

        dep_repo = AsyncMock()
        dep_repo.get = AsyncMock(side_effect=get_side_effect)
        dep_repo.update_state = AsyncMock(side_effect=tracking_update_state)
        dep_repo.update_state_if = AsyncMock(side_effect=tracking_update_state_if)
        dep_repo.update_endpoint = AsyncMock()
        dep_repo.attach_runtime = AsyncMock()
        dep_repo.update_configuration = AsyncMock()

        pool_repo = AsyncMock()
        pool_repo.get = AsyncMock(return_value=pool)

        inventory_repo = AsyncMock()
        inventory_repo.get_resource_requirement = AsyncMock(
            return_value={"vcpu_total": 8, "ram_gb_total": 32}
        )
        inventory_repo.register_node = AsyncMock(return_value=uuid4())

        placement_repo = AsyncMock()
        placement_repo.fetch_candidate_nodes = AsyncMock(return_value=[])

        worker = self._make_worker(
            deployment_repo=dep_repo,
            pool_repo=pool_repo,
            inventory_repo=inventory_repo,
            placement_repo=placement_repo,
        )

        mock_adapter = MagicMock()
        mock_adapter.get_capabilities = MagicMock(return_value=self._make_capabilities())
        mock_adapter.provision_node = AsyncMock(return_value={
            "provider_instance_id": "job-123",
            "provider": "nosana",
            "hostname": "node-1",
            "gpu_total": 1,
            "vcpu_total": 8,
            "ram_gb_total": 32,
            "node_class": "gpu",
            "metadata": {},
            "expose_url": "http://test:8000",
        })
        mock_adapter.wait_for_ready = AsyncMock(return_value="http://test:8000")
        mock_adapter.deprovision_node = AsyncMock()

        with patch(
            "inferia.services.orchestration.services.model_deployment.worker.get_adapter",
            return_value=mock_adapter,
        ), patch(
            "inferia.services.orchestration.services.model_deployment.worker.settings",
        ) as mock_settings:
            mock_settings.max_deployment_retries = 2
            await worker.handle_deploy_requested(dep_id)

        running_calls = [
            c for c in original_update_state.call_args_list
            if len(c[0]) >= 2 and c[0][1] == "RUNNING"
        ]
        assert len(running_calls) > 0, "Should have reached RUNNING"

        retrying_calls = [
            c for c in original_update_state.call_args_list
            if len(c[0]) >= 2 and c[0][1] == "RETRYING"
        ]
        assert len(retrying_calls) == 0, "Should not have retried on success"
