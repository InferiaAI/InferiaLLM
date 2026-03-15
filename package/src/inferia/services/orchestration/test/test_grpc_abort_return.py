"""Tests for gRPC context.abort() execution-stop bug (issue #34).

In gRPC-aio, context.abort() does NOT raise an exception — the handler
must return explicitly. These tests use a non-raising mock to verify
that execution stops after abort (no invalid DB writes, no crashes).
"""

import json
import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock


def make_non_raising_context():
    """Mock gRPC context where abort() does NOT raise (real gRPC-aio behavior)."""
    ctx = MagicMock()
    ctx.abort = MagicMock()  # does not raise, just records the call
    return ctx


# ── ModelRegistryService ──────────────────────────────────────────


from inferia.services.orchestration.services.model_registry.service import (
    ModelRegistryService,
)


class TestModelRegistryAbortReturn:
    @pytest.mark.asyncio
    async def test_register_model_invalid_backend_does_not_write_db(self):
        """After aborting for invalid backend, repo.register_model must NOT be called."""
        repo = AsyncMock()
        svc = ModelRegistryService(repo)

        request = MagicMock()
        request.backend = "INVALID_BACKEND"
        request.name = "test"
        request.version = "v1"
        request.config_json = ""
        request.artifact_uri = "s3://bucket/model"

        ctx = make_non_raising_context()
        await svc.RegisterModel(request, ctx)

        ctx.abort.assert_called_once()
        repo.register_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_model_not_found_does_not_crash(self):
        """After aborting for model not found, must not access None dict keys."""
        repo = AsyncMock()
        repo.get_model = AsyncMock(return_value=None)
        svc = ModelRegistryService(repo)

        request = MagicMock()
        request.name = "nonexistent"
        request.version = "v1"

        ctx = make_non_raising_context()
        # Should NOT raise TypeError from None["model_id"]
        await svc.GetModel(request, ctx)

        ctx.abort.assert_called_once()


# ── ComputeNodeService ────────────────────────────────────────────


from inferia.services.orchestration.services.compute_node.service import (
    ComputeNodeService,
)


class TestComputeNodeAbortReturn:
    @pytest.mark.asyncio
    async def test_heartbeat_node_not_found_does_not_crash(self):
        """After aborting for node not found, must not access None['state']."""
        inventory_repo = AsyncMock()
        inventory_repo.get = AsyncMock(return_value=None)
        svc = ComputeNodeService(inventory_repo)

        request = MagicMock()
        request.node_id = str(uuid4())
        request.used = {}

        ctx = make_non_raising_context()
        # Should NOT raise TypeError from None["state"]
        await svc.Heartbeat(request, ctx)

        ctx.abort.assert_called_once()
        inventory_repo.mark_ready.assert_not_called()
        inventory_repo.update_heartbeat.assert_not_called()
        inventory_repo.update_usage.assert_not_called()


# ── InventoryManagerService ───────────────────────────────────────


from inferia.services.orchestration.services.inventory_manager.service import (
    InventoryManagerService,
)


class TestInventoryManagerAbortReturn:
    @pytest.mark.asyncio
    async def test_heartbeat_node_not_found_does_not_crash(self):
        """After aborting for node not found, must not access None['state']."""
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=None)
        event_bus = AsyncMock()
        svc = InventoryManagerService(repo, event_bus)

        request = MagicMock()
        request.node_id = str(uuid4())
        request.gpu_allocated = 0
        request.vcpu_allocated = 0
        request.ram_gb_allocated = 0
        request.health_score = 100

        ctx = make_non_raising_context()
        # Should NOT raise TypeError from None["state"]
        await svc.InvenHeartbeat(request, ctx)

        ctx.abort.assert_called_once()
        repo.mark_ready.assert_not_called()
        repo.update_heartbeat.assert_not_called()
        repo.update_usage.assert_not_called()
        event_bus.publish.assert_not_called()
