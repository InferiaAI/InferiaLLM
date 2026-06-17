"""Unit tests for ``_initiate_node_destroy`` DePIN (nosana) teardown.

``_initiate_node_destroy`` is the SHARED choke point every user-facing
deployment delete/terminate path routes node teardown through
(``terminate_deployment_core`` PENDING_NODE/RUNNING branches +
``delete_deployment`` C9). For a CLOUD node it flips the node's provisioning
job to 'cancelling' so the reconciler destroys the Pulumi stack. For a DePIN
node (nosana/akash/k8s) there is NO reconciler job — force_cancel would no-op
and the external PAID job would keep billing. These tests assert the DePIN
branch instead deprovisions the external job INLINE (adapter.deprovision_node)
and marks the node terminated, with NO force_cancel.

No database: a fake db_pool serves the inventory + pool reads and records the
UPDATEs; the adapter is an AsyncMock(spec=NosanaAdapter).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.models.model_deployment import deployment_server
from orchestration.provisioning.engine import registry as engine_registry
from providers.nosana.nosana_adapter import NosanaAdapter

pytestmark = pytest.mark.asyncio

NODE_ID = "11111111-2222-3333-4444-555555555555"
POOL_ID = "942d7675-5633-4a72-a5e7-defbf4866ab5"


class _FakeConn:
    """Serves the inventory fetchrow + pool fetchval and records executes."""

    def __init__(self, *, node_row, pool_cred):
        self._node_row = node_row
        self._pool_cred = pool_cred
        self.executed: list[str] = []

    async def fetchrow(self, sql, *args):
        if "FROM compute_inventory" in sql:
            return self._node_row
        return None

    async def fetchval(self, sql, *args):
        if "provider_credential_name" in sql:
            return self._pool_cred
        return None

    async def execute(self, sql, *args):
        self.executed.append(" ".join(sql.split()))
        return "UPDATE 1"


class _FakeDbPool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self_):
                return conn

            async def __aexit__(self_, *a):
                return False

        return _Ctx()


def _node_row(pii="job-addr-123"):
    return {
        "id": NODE_ID,
        "provider": "nosana",
        "provider_instance_id": pii,
        "pool_id": POOL_ID,
    }


async def test_depin_node_deprovisions_inline_then_terminates():
    conn = _FakeConn(node_row=_node_row(), pool_cred="nosana-cred-1")
    db_pool = _FakeDbPool(conn)
    jobs_repo = MagicMock()
    jobs_repo.force_cancel = AsyncMock()
    fake_adapter = AsyncMock(spec=NosanaAdapter)

    with patch.object(engine_registry, "get_adapter", return_value=fake_adapter):
        ok = await deployment_server._initiate_node_destroy(
            db_pool=db_pool,
            jobs_repo=jobs_repo,
            node_id=NODE_ID,
            pool_id=POOL_ID,
            org_id=None,
            provider="nosana",
        )

    assert ok is True
    fake_adapter.deprovision_node.assert_awaited_once_with(
        provider_instance_id="job-addr-123",
        provider_credential_name="nosana-cred-1",
    )
    # DePIN never routes through the reconciler force_cancel.
    jobs_repo.force_cancel.assert_not_called()
    # Node marked terminated.
    assert any("state='terminated'" in s for s in conn.executed)


async def test_depin_placeholder_skips_deprovision_still_terminates():
    conn = _FakeConn(node_row=_node_row("placeholder:x"), pool_cred="c")
    db_pool = _FakeDbPool(conn)
    jobs_repo = MagicMock()
    jobs_repo.force_cancel = AsyncMock()
    fake_adapter = AsyncMock(spec=NosanaAdapter)

    with patch.object(engine_registry, "get_adapter", return_value=fake_adapter):
        ok = await deployment_server._initiate_node_destroy(
            db_pool=db_pool, jobs_repo=jobs_repo, node_id=NODE_ID,
            pool_id=POOL_ID, org_id=None, provider="nosana",
        )

    assert ok is True
    fake_adapter.deprovision_node.assert_not_called()
    assert any("state='terminated'" in s for s in conn.executed)


async def test_depin_deprovision_failure_marks_failed_still_terminates():
    conn = _FakeConn(node_row=_node_row(), pool_cred="c")
    db_pool = _FakeDbPool(conn)
    jobs_repo = MagicMock()
    jobs_repo.force_cancel = AsyncMock()
    fake_adapter = AsyncMock(spec=NosanaAdapter)
    fake_adapter.deprovision_node.side_effect = RuntimeError("sidecar down")

    with patch.object(engine_registry, "get_adapter", return_value=fake_adapter):
        ok = await deployment_server._initiate_node_destroy(
            db_pool=db_pool, jobs_repo=jobs_repo, node_id=NODE_ID,
            pool_id=POOL_ID, org_id=None, provider="nosana",
        )

    # Idempotent delete still completes; the leak is surfaced via the marker.
    assert ok is True
    fake_adapter.deprovision_node.assert_awaited_once()
    assert any(
        "state='terminated'" in s and "deprovision_failed" in s
        for s in conn.executed
    )


async def test_cloud_provider_unchanged_uses_force_cancel():
    """Regression lock: a CLOUD (aws) node still routes through force_cancel,
    NOT the DePIN inline deprovision."""
    conn = _FakeConn(node_row=None, pool_cred=None)
    db_pool = _FakeDbPool(conn)
    jobs_repo = MagicMock()
    jobs_repo.force_cancel = AsyncMock(return_value=True)
    fake_adapter = AsyncMock(spec=NosanaAdapter)

    with patch.object(engine_registry, "get_adapter", return_value=fake_adapter):
        ok = await deployment_server._initiate_node_destroy(
            db_pool=db_pool, jobs_repo=jobs_repo, node_id=NODE_ID,
            pool_id=POOL_ID, org_id=None, provider="aws",
        )

    assert ok is True
    jobs_repo.force_cancel.assert_awaited_once()
    fake_adapter.deprovision_node.assert_not_called()
