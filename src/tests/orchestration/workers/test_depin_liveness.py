from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestration.workers import depin_liveness_worker as mod
from orchestration.repositories.model_deployment_repo import (
    ModelDeploymentRepository,
)
from orchestration.repositories.inventory_repo import InventoryRepository
from orchestration.repositories.pool_repo import ComputePoolRepository
from providers.nosana.nosana_adapter import NosanaAdapter

pytestmark = pytest.mark.asyncio


def _mocks(*, deploy_rows, node, pool):
    deploys = AsyncMock(spec=ModelDeploymentRepository)
    deploys.list_by_state.return_value = deploy_rows
    deploys.update_state_if.return_value = True
    inventory = AsyncMock(spec=InventoryRepository)
    inventory.get_node_by_id.return_value = node
    pool_repo = AsyncMock(spec=ComputePoolRepository)
    pool_repo.get.return_value = pool
    return deploys, inventory, pool_repo


def _adapter(status):
    a = AsyncMock(spec=NosanaAdapter)
    a.get_node_status = AsyncMock(return_value=status)
    return a


@pytest.mark.parametrize("status", ["COMPLETED", "STOPPED", "QUIT", "FAILED"])
async def test_terminal_job_fails_and_deprovisions(monkeypatch, status):
    deploys, inventory, pool_repo = _mocks(
        deploy_rows=[{"deployment_id": "d1", "pool_id": "p1", "target_node_id": "n1"}],
        node={"provider_instance_id": "job-abc", "provider": "nosana"},
        pool={"provider": "nosana", "provider_credential_name": "cred1"},
    )
    adapter = _adapter(status)
    dep = AsyncMock(return_value=(False, "already terminal"))
    monkeypatch.setattr(mod, "_deprovision_direct_node", dep)

    w = mod.DepinLivenessWorker(
        deploys=deploys, inventory=inventory, pool_repo=pool_repo,
        get_adapter_fn=lambda p: adapter,
    )
    await w.reconcile_once()

    adapter.get_node_status.assert_awaited_once()
    assert adapter.get_node_status.await_args.kwargs["provider_instance_id"] == "job-abc"
    assert adapter.get_node_status.await_args.kwargs["provider_credential_name"] == "cred1"
    deploys.update_state_if.assert_awaited_once()
    assert deploys.update_state_if.await_args.args[:3] == ("d1", "RUNNING", "FAILED")
    dep.assert_awaited_once()
    inventory.mark_terminated.assert_awaited_once_with("n1")


async def test_redeploy_swap_skips_fail(monkeypatch):
    # Read-after-confirm: the node's job changed (SIMPLE-EXTEND redeploy) between
    # the status check and the action -> deployment is recovering -> do NOT fail.
    deploys = AsyncMock(spec=ModelDeploymentRepository)
    deploys.list_by_state.return_value = [
        {"deployment_id": "d1", "pool_id": "p1", "target_node_id": "n1"}
    ]
    deploys.update_state_if.return_value = True
    inventory = AsyncMock(spec=InventoryRepository)
    # first read (in _check_one): old job; re-read (after terminal): NEW job id
    inventory.get_node_by_id.side_effect = [
        {"provider_instance_id": "job-old", "provider": "nosana"},
        {"provider_instance_id": "job-new", "provider": "nosana"},
    ]
    pool_repo = AsyncMock(spec=ComputePoolRepository)
    pool_repo.get.return_value = {"provider": "nosana", "provider_credential_name": "c"}
    dep = AsyncMock()
    monkeypatch.setattr(mod, "_deprovision_direct_node", dep)

    w = mod.DepinLivenessWorker(
        deploys=deploys, inventory=inventory, pool_repo=pool_repo,
        get_adapter_fn=lambda p: _adapter("COMPLETED"),
    )
    await w.reconcile_once()

    deploys.update_state_if.assert_not_awaited()
    dep.assert_not_awaited()


async def test_already_transitioned_skips_deprovision(monkeypatch):
    # update_state_if returns False (another flow already moved it) -> no teardown.
    deploys, inventory, pool_repo = _mocks(
        deploy_rows=[{"deployment_id": "d1", "pool_id": "p1", "target_node_id": "n1"}],
        node={"provider_instance_id": "job-abc", "provider": "nosana"},
        pool={"provider": "nosana", "provider_credential_name": "cred1"},
    )
    deploys.update_state_if.return_value = False
    dep = AsyncMock()
    monkeypatch.setattr(mod, "_deprovision_direct_node", dep)

    w = mod.DepinLivenessWorker(
        deploys=deploys, inventory=inventory, pool_repo=pool_repo,
        get_adapter_fn=lambda p: _adapter("COMPLETED"),
    )
    await w.reconcile_once()

    deploys.update_state_if.assert_awaited_once()
    dep.assert_not_awaited()
    inventory.mark_terminated.assert_not_awaited()


@pytest.mark.parametrize("status", ["RUNNING", "QUEUED", "unknown"])
async def test_non_terminal_job_left_alone(monkeypatch, status):
    deploys, inventory, pool_repo = _mocks(
        deploy_rows=[{"deployment_id": "d1", "pool_id": "p1", "target_node_id": "n1"}],
        node={"provider_instance_id": "job-abc", "provider": "nosana"},
        pool={"provider": "nosana", "provider_credential_name": "cred1"},
    )
    adapter = _adapter(status)
    dep = AsyncMock()
    monkeypatch.setattr(mod, "_deprovision_direct_node", dep)

    w = mod.DepinLivenessWorker(
        deploys=deploys, inventory=inventory, pool_repo=pool_repo,
        get_adapter_fn=lambda p: adapter,
    )
    await w.reconcile_once()

    deploys.update_state_if.assert_not_awaited()
    dep.assert_not_awaited()
    inventory.mark_terminated.assert_not_awaited()


async def test_non_depin_provider_skipped(monkeypatch):
    deploys, inventory, pool_repo = _mocks(
        deploy_rows=[{"deployment_id": "d1", "pool_id": "p1", "target_node_id": "n1"}],
        node={"provider_instance_id": "i-123", "provider": "aws"},
        pool={"provider": "aws", "provider_credential_name": None},
    )
    adapter = _adapter("COMPLETED")  # must never be consulted for aws
    dep = AsyncMock()
    monkeypatch.setattr(mod, "_deprovision_direct_node", dep)

    w = mod.DepinLivenessWorker(
        deploys=deploys, inventory=inventory, pool_repo=pool_repo,
        get_adapter_fn=lambda p: adapter,
    )
    await w.reconcile_once()

    adapter.get_node_status.assert_not_awaited()
    deploys.update_state_if.assert_not_awaited()
    dep.assert_not_awaited()


async def test_placeholder_node_skipped(monkeypatch):
    deploys, inventory, pool_repo = _mocks(
        deploy_rows=[{"deployment_id": "d1", "pool_id": "p1", "target_node_id": "n1"}],
        node={"provider_instance_id": "placeholder:abc", "provider": "nosana"},
        pool={"provider": "nosana", "provider_credential_name": "cred1"},
    )
    adapter = _adapter("COMPLETED")
    monkeypatch.setattr(mod, "_deprovision_direct_node", AsyncMock())

    w = mod.DepinLivenessWorker(
        deploys=deploys, inventory=inventory, pool_repo=pool_repo,
        get_adapter_fn=lambda p: adapter,
    )
    await w.reconcile_once()

    adapter.get_node_status.assert_not_awaited()
    deploys.update_state_if.assert_not_awaited()


async def test_no_bound_node_skipped(monkeypatch):
    deploys, inventory, pool_repo = _mocks(
        deploy_rows=[{"deployment_id": "d1", "pool_id": "p1", "target_node_id": None, "node_ids": None}],
        node=None,
        pool={"provider": "nosana", "provider_credential_name": "cred1"},
    )
    monkeypatch.setattr(mod, "_deprovision_direct_node", AsyncMock())
    w = mod.DepinLivenessWorker(
        deploys=deploys, inventory=inventory, pool_repo=pool_repo,
        get_adapter_fn=lambda p: _adapter("COMPLETED"),
    )
    await w.reconcile_once()
    deploys.update_state_if.assert_not_awaited()


async def test_uses_node_ids_when_no_target_node(monkeypatch):
    deploys, inventory, pool_repo = _mocks(
        deploy_rows=[{"deployment_id": "d1", "pool_id": "p1", "target_node_id": None, "node_ids": ["nX"]}],
        node={"provider_instance_id": "job-xyz", "provider": "nosana"},
        pool={"provider": "nosana", "provider_credential_name": "cred1"},
    )
    adapter = _adapter("STOPPED")
    monkeypatch.setattr(mod, "_deprovision_direct_node", AsyncMock())
    w = mod.DepinLivenessWorker(
        deploys=deploys, inventory=inventory, pool_repo=pool_repo,
        get_adapter_fn=lambda p: adapter,
    )
    await w.reconcile_once()
    inventory.get_node_by_id.assert_any_await("nX")
    deploys.update_state_if.assert_awaited_once()


async def test_one_bad_deployment_does_not_stop_others(monkeypatch):
    # First row blows up (pool_repo.get raises); second is a healthy terminal one.
    deploys = AsyncMock(spec=ModelDeploymentRepository)
    deploys.list_by_state.return_value = [
        {"deployment_id": "bad", "pool_id": "pbad", "target_node_id": "nbad"},
        {"deployment_id": "d2", "pool_id": "p2", "target_node_id": "n2"},
    ]
    inventory = AsyncMock(spec=InventoryRepository)
    inventory.get_node_by_id.return_value = {"provider_instance_id": "job2", "provider": "nosana"}
    pool_repo = AsyncMock(spec=ComputePoolRepository)

    async def _get(pid):
        if pid == "pbad":
            raise RuntimeError("boom")
        return {"provider": "nosana", "provider_credential_name": "c"}
    pool_repo.get.side_effect = _get
    deploys.update_state_if.return_value = True

    monkeypatch.setattr(mod, "_deprovision_direct_node", AsyncMock())
    w = mod.DepinLivenessWorker(
        deploys=deploys, inventory=inventory, pool_repo=pool_repo,
        get_adapter_fn=lambda p: _adapter("QUIT"),
    )
    await w.reconcile_once()
    # the good one still got reconciled despite the bad one raising
    deploys.update_state_if.assert_awaited_once()
    assert deploys.update_state_if.await_args.args[0] == "d2"


# ---- NosanaAdapter.get_node_status ----

class _FakeResp:
    def __init__(self, status, payload):
        self._s, self._p = status, payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @property
    def status(self):
        return self._s

    async def json(self):
        return self._p


class _FakeSession:
    def __init__(self, resp):
        self._r = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, *a, **k):
        return self._r


async def test_get_node_status_normalizes_running(monkeypatch):
    import providers.nosana.nosana_adapter as na
    monkeypatch.setattr(na.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(_FakeResp(200, {"jobState": 1})))
    out = await NosanaAdapter().get_node_status(provider_instance_id="job1")
    assert out == "RUNNING"


async def test_get_node_status_terminal(monkeypatch):
    import providers.nosana.nosana_adapter as na
    monkeypatch.setattr(na.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(_FakeResp(200, {"jobState": 2})))
    out = await NosanaAdapter().get_node_status(provider_instance_id="job1")
    assert out == "COMPLETED"


async def test_get_node_status_non_200_is_unknown(monkeypatch):
    import providers.nosana.nosana_adapter as na
    monkeypatch.setattr(na.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(_FakeResp(503, {})))
    out = await NosanaAdapter().get_node_status(provider_instance_id="job1")
    assert out == "unknown"


async def test_get_node_status_error_is_unknown(monkeypatch):
    import providers.nosana.nosana_adapter as na

    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(na.aiohttp, "ClientSession", _boom)
    out = await NosanaAdapter().get_node_status(provider_instance_id="job1")
    assert out == "unknown"


async def test_base_adapter_get_node_status_default_unknown():
    # WorkerAdapter doesn't override -> base default "unknown" (never acted on)
    from providers.worker.worker_adapter import WorkerAdapter
    out = await WorkerAdapter().get_node_status(provider_instance_id="x")
    assert out == "unknown"


# ---- NosanaAdapter.get_logs messaging (T14) ----

class _FakeSeqSession:
    """ClientSession whose .get() yields a queued response per call."""

    def __init__(self, responses):
        self._r = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, *a, **k):
        return self._r.pop(0)


async def test_get_logs_terminal_message_is_accurate(monkeypatch):
    import providers.nosana.nosana_adapter as na
    # /logs -> non-200 (skip), then /{addr} -> terminal jobState=2 (COMPLETED)
    seq = [_FakeResp(503, {}), _FakeResp(200, {"jobState": 2})]
    monkeypatch.setattr(na.aiohttp, "ClientSession", lambda *a, **k: _FakeSeqSession(seq))
    out = await NosanaAdapter().get_logs(provider_instance_id="job1")
    text = " ".join(str(x) for x in out["logs"]).lower()
    assert "does not retain" in text
    assert "historical logs" not in text  # the old misleading claim is gone
    assert out["job_state"] == "COMPLETED"


async def test_get_logs_pending_returns_running(monkeypatch):
    import providers.nosana.nosana_adapter as na
    seq = [_FakeResp(200, {"status": "pending", "logs": ["Job is running..."]})]
    monkeypatch.setattr(na.aiohttp, "ClientSession", lambda *a, **k: _FakeSeqSession(seq))
    out = await NosanaAdapter().get_logs(provider_instance_id="job1")
    assert any("running" in str(x).lower() for x in out["logs"])
