"""Tests for terminal log persistence on deployment failure/stop (#169)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from inferia.services.orchestration.repositories.terminal_log_repo import (
    TerminalLogRepository,
)


def _mock_db(rows=None):
    """Create a mock DB pool."""
    db = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=rows[0] if rows else None)
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    db.acquire = MagicMock(return_value=conn)
    return db, conn


class TestTerminalLogRepository:
    @pytest.mark.asyncio
    async def test_save_inserts_log(self):
        db, conn = _mock_db()
        repo = TerminalLogRepository(db)

        dep_id = uuid4()
        await repo.save(
            deployment_id=dep_id,
            log_lines=["Starting...", "Error: OOM"],
            trigger_event="FAILED",
        )

        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args[0]
        assert dep_id in call_args
        assert ["Starting...", "Error: OOM"] in call_args
        assert "FAILED" in call_args

    @pytest.mark.asyncio
    async def test_save_with_tx_uses_tx_connection(self):
        db, _ = _mock_db()
        repo = TerminalLogRepository(db)
        tx = AsyncMock()

        await repo.save(
            deployment_id=uuid4(),
            log_lines=["line1"],
            trigger_event="STOPPED",
            tx=tx,
        )

        tx.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_without_tx_uses_pool(self):
        db, conn = _mock_db()
        repo = TerminalLogRepository(db)

        await repo.save(
            deployment_id=uuid4(),
            log_lines=["line1"],
            trigger_event="FAILED",
        )

        db.acquire.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_by_deployment_returns_dict(self):
        fake_row = {
            "id": uuid4(),
            "deployment_id": uuid4(),
            "log_lines": ["log1", "log2"],
            "captured_at": "2026-04-01T00:00:00",
            "trigger_event": "FAILED",
        }
        db, conn = _mock_db([fake_row])
        repo = TerminalLogRepository(db)

        result = await repo.get_by_deployment(fake_row["deployment_id"])

        assert result is not None
        assert result["log_lines"] == ["log1", "log2"]
        assert result["trigger_event"] == "FAILED"

    @pytest.mark.asyncio
    async def test_get_by_deployment_returns_none_when_empty(self):
        db, conn = _mock_db()
        conn.fetchrow = AsyncMock(return_value=None)
        repo = TerminalLogRepository(db)

        result = await repo.get_by_deployment(uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_get_all_by_deployment_returns_list(self):
        rows = [
            {
                "id": uuid4(),
                "deployment_id": uuid4(),
                "log_lines": ["log1"],
                "captured_at": "2026-04-01T00:00:00",
                "trigger_event": "FAILED",
            },
            {
                "id": uuid4(),
                "deployment_id": uuid4(),
                "log_lines": ["log2"],
                "captured_at": "2026-04-01T01:00:00",
                "trigger_event": "STOPPED",
            },
        ]
        db, conn = _mock_db(rows)
        repo = TerminalLogRepository(db)

        result = await repo.get_all_by_deployment(uuid4())
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_all_by_deployment_empty(self):
        db, conn = _mock_db([])
        repo = TerminalLogRepository(db)

        result = await repo.get_all_by_deployment(uuid4())
        assert result == []


class TestWorkerLogPersistence:
    @pytest.mark.asyncio
    async def test_persist_terminal_logs_called_on_failure(self):
        """Worker should persist logs before transitioning to FAILED."""
        from inferia.services.orchestration.services.model_deployment.worker import (
            ModelDeploymentWorker,
        )

        mock_logs = AsyncMock()
        mock_deployments = AsyncMock()
        mock_deployments.get = AsyncMock(
            return_value={
                "state": "PROVISIONING",
                "node_ids": [uuid4()],
                "pool_id": uuid4(),
            }
        )
        mock_pools = AsyncMock()
        mock_pools.get = AsyncMock(
            return_value={"provider": "k8s", "provider_credential_name": None}
        )
        mock_inventory = AsyncMock()
        mock_inventory.get_node = AsyncMock(
            return_value={"provider_instance_id": "pod-123"}
        )

        worker = ModelDeploymentWorker(
            deployment_repo=mock_deployments,
            model_registry_repo=AsyncMock(),
            pool_repo=mock_pools,
            placement_repo=AsyncMock(),
            scheduler=AsyncMock(),
            inventory_repo=mock_inventory,
            runtime_resolver=MagicMock(),
            runtime_strategies={},
            terminal_log_repo=mock_logs,
        )

        mock_adapter = MagicMock()
        mock_adapter.get_logs = AsyncMock(return_value={"logs": ["error line"]})

        with patch(
            "inferia.services.orchestration.services.model_deployment.worker.get_adapter",
            return_value=mock_adapter,
        ):
            await worker._persist_terminal_logs(uuid4(), "FAILED")

        mock_logs.save.assert_awaited_once()
        call_kwargs = mock_logs.save.call_args[1]
        assert call_kwargs["trigger_event"] == "FAILED"
        assert call_kwargs["log_lines"] == ["error line"]

    @pytest.mark.asyncio
    async def test_persist_terminal_logs_skips_without_repo(self):
        """If terminal_log_repo is None, persist should be a no-op."""
        from inferia.services.orchestration.services.model_deployment.worker import (
            ModelDeploymentWorker,
        )

        worker = ModelDeploymentWorker(
            deployment_repo=AsyncMock(),
            model_registry_repo=AsyncMock(),
            pool_repo=AsyncMock(),
            placement_repo=AsyncMock(),
            scheduler=AsyncMock(),
            inventory_repo=AsyncMock(),
            runtime_resolver=MagicMock(),
            runtime_strategies={},
            terminal_log_repo=None,
        )

        # Should not raise
        await worker._persist_terminal_logs(uuid4(), "FAILED")

    @pytest.mark.asyncio
    async def test_persist_terminal_logs_handles_error_gracefully(self):
        """Errors during log persistence should be swallowed (best-effort)."""
        from inferia.services.orchestration.services.model_deployment.worker import (
            ModelDeploymentWorker,
        )

        mock_logs = AsyncMock()
        mock_deployments = AsyncMock()
        mock_deployments.get = AsyncMock(side_effect=Exception("DB down"))

        worker = ModelDeploymentWorker(
            deployment_repo=mock_deployments,
            model_registry_repo=AsyncMock(),
            pool_repo=AsyncMock(),
            placement_repo=AsyncMock(),
            scheduler=AsyncMock(),
            inventory_repo=AsyncMock(),
            runtime_resolver=MagicMock(),
            runtime_strategies={},
            terminal_log_repo=mock_logs,
        )

        # Should NOT raise
        await worker._persist_terminal_logs(uuid4(), "FAILED")

    @pytest.mark.asyncio
    async def test_persist_skips_without_node_ids(self):
        """If deployment has no node_ids, persist should be a no-op."""
        from inferia.services.orchestration.services.model_deployment.worker import (
            ModelDeploymentWorker,
        )

        mock_logs = AsyncMock()
        mock_deployments = AsyncMock()
        mock_deployments.get = AsyncMock(
            return_value={"state": "FAILED", "node_ids": [], "pool_id": uuid4()}
        )

        worker = ModelDeploymentWorker(
            deployment_repo=mock_deployments,
            model_registry_repo=AsyncMock(),
            pool_repo=AsyncMock(),
            placement_repo=AsyncMock(),
            scheduler=AsyncMock(),
            inventory_repo=AsyncMock(),
            runtime_resolver=MagicMock(),
            runtime_strategies={},
            terminal_log_repo=mock_logs,
        )

        await worker._persist_terminal_logs(uuid4(), "FAILED")
        mock_logs.save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_persist_truncates_to_1000_lines(self):
        """Log lines should be capped at 1000."""
        from inferia.services.orchestration.services.model_deployment.worker import (
            ModelDeploymentWorker,
        )

        mock_logs = AsyncMock()
        mock_deployments = AsyncMock()
        mock_deployments.get = AsyncMock(
            return_value={
                "state": "RUNNING",
                "node_ids": [uuid4()],
                "pool_id": uuid4(),
            }
        )
        mock_pools = AsyncMock()
        mock_pools.get = AsyncMock(
            return_value={"provider": "k8s", "provider_credential_name": None}
        )
        mock_inventory = AsyncMock()
        mock_inventory.get_node = AsyncMock(
            return_value={"provider_instance_id": "pod-big"}
        )

        worker = ModelDeploymentWorker(
            deployment_repo=mock_deployments,
            model_registry_repo=AsyncMock(),
            pool_repo=mock_pools,
            placement_repo=AsyncMock(),
            scheduler=AsyncMock(),
            inventory_repo=mock_inventory,
            runtime_resolver=MagicMock(),
            runtime_strategies={},
            terminal_log_repo=mock_logs,
        )

        many_lines = [f"line {i}" for i in range(2000)]
        mock_adapter = MagicMock()
        mock_adapter.get_logs = AsyncMock(return_value={"logs": many_lines})

        with patch(
            "inferia.services.orchestration.services.model_deployment.worker.get_adapter",
            return_value=mock_adapter,
        ):
            await worker._persist_terminal_logs(uuid4(), "STOPPED")

        call_kwargs = mock_logs.save.call_args[1]
        assert len(call_kwargs["log_lines"]) == 1000
