"""POST /createpool with provider=aws must:
1. Insert a placeholder inventory row with state='provisioning' and gpu_total=0.
2. Call PulumiAWSAdapter.provision_node with a progress_writer.
3. Leave existing nosana/akash placeholder path with state='ready' unchanged.
"""
from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


class FakeConn:
    def __init__(self):
        self.executes = []
    async def execute(self, q, *a):
        self.executes.append((q, a))
    async def fetchval(self, q, *a):
        return 1
    async def fetch(self, q, *a):
        return []
    async def close(self):
        pass


@pytest.mark.asyncio
async def test_createpool_aws_inserts_provisioning_placeholder(monkeypatch):
    from inferia.services.orchestration.services.model_deployment import deployment_server
    fake_conn = FakeConn()
    asyncpg_mock = MagicMock()
    asyncpg_mock.connect = AsyncMock(return_value=fake_conn)
    monkeypatch.setattr(deployment_server, "asyncpg", asyncpg_mock, raising=False)

    mock_adapter = MagicMock()
    mock_adapter.get_capabilities.return_value = MagicMock()
    mock_adapter.provision_node = AsyncMock(return_value={"lifecycle_state": "provisioning"})
    monkeypatch.setattr(deployment_server, "get_adapter", lambda p: mock_adapter)
    # Also patch ADAPTER_REGISTRY so the per-call instantiation path returns
    # a class whose constructor yields mock_adapter.
    mock_aws_cls = MagicMock(return_value=mock_adapter)
    monkeypatch.setattr(deployment_server, "ADAPTER_REGISTRY", {"aws": mock_aws_cls})

    fake_stub = MagicMock()
    fake_stub.RegisterPool = AsyncMock(return_value=MagicMock(pool_id="00000000-0000-0000-0000-00000000aaaa"))
    fake_channel_cm = MagicMock()
    fake_channel_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    fake_channel_cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(deployment_server, "_auth_channel", MagicMock(return_value=fake_channel_cm))
    monkeypatch.setattr(
        deployment_server.compute_pool_pb2_grpc,
        "ComputePoolManagerStub",
        lambda channel: fake_stub,
    )
    monkeypatch.setattr(deployment_server, "log_audit_event", AsyncMock())

    req_body = {
        "pool_name": "aws-test",
        "owner_type": "user",
        "owner_id": "00000000-0000-0000-0000-000000000001",
        "provider": "aws",
        "allowed_gpu_types": ["t3.micro"],
        "gpu_count": 1,
        "max_cost_per_hour": 0.5,
        "is_dedicated": False,
        "provider_pool_id": "",
        "scheduling_policy_json": "{}",
    }
    request = MagicMock()
    request.headers = {"x-organization-id": None}

    resp = await deployment_server.create_pool(
        deployment_server.CreatePoolRequest(**req_body), request
    )
    assert resp == {"pool_id": "00000000-0000-0000-0000-00000000aaaa", "status": "CREATED"}

    inserts = [q for q, _ in fake_conn.executes if "INSERT INTO compute_inventory" in q]
    assert any("'provisioning'" in q for q in inserts), \
        f"expected placeholder INSERT with state='provisioning', got: {inserts}"

    # Let any spawned asyncio.create_task settle
    await asyncio.sleep(0.05)
    mock_adapter.provision_node.assert_awaited_once()
    call = mock_adapter.provision_node.await_args
    assert call.kwargs.get("progress_writer") is not None
    assert call.kwargs.get("pool_id") == "00000000-0000-0000-0000-00000000aaaa"


@pytest.mark.asyncio
async def test_createpool_nosana_keeps_ready_placeholder(monkeypatch):
    """Regression: non-AWS providers keep the old behaviour."""
    from inferia.services.orchestration.services.model_deployment import deployment_server
    fake_conn = FakeConn()
    asyncpg_mock = MagicMock()
    asyncpg_mock.connect = AsyncMock(return_value=fake_conn)
    monkeypatch.setattr(deployment_server, "asyncpg", asyncpg_mock, raising=False)

    mock_adapter = MagicMock()
    mock_adapter.get_capabilities.return_value = MagicMock()
    mock_adapter.provision_node = AsyncMock()
    monkeypatch.setattr(deployment_server, "get_adapter", lambda p: mock_adapter)

    fake_stub = MagicMock()
    fake_stub.RegisterPool = AsyncMock(return_value=MagicMock(pool_id="00000000-0000-0000-0000-00000000bbbb"))
    fake_channel_cm = MagicMock()
    fake_channel_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    fake_channel_cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(deployment_server, "_auth_channel", MagicMock(return_value=fake_channel_cm))
    monkeypatch.setattr(
        deployment_server.compute_pool_pb2_grpc,
        "ComputePoolManagerStub",
        lambda channel: fake_stub,
    )
    monkeypatch.setattr(deployment_server, "log_audit_event", AsyncMock())

    req_body = {
        "pool_name": "nosana-test",
        "owner_type": "user",
        "owner_id": "00000000-0000-0000-0000-000000000001",
        "provider": "nosana",
        "allowed_gpu_types": ["a100"],
        "gpu_count": 1,
        "max_cost_per_hour": 0.5,
        "is_dedicated": False,
        "provider_pool_id": "",
        "scheduling_policy_json": "{}",
    }
    request = MagicMock(); request.headers = {"x-organization-id": None}
    await deployment_server.create_pool(
        deployment_server.CreatePoolRequest(**req_body), request
    )

    inserts = [q for q, _ in fake_conn.executes if "INSERT INTO compute_inventory" in q]
    assert any("'ready'" in q for q in inserts), f"expected placeholder with state='ready', got: {inserts}"
    mock_adapter.provision_node.assert_not_called()
