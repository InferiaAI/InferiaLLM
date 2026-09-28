"""Tests for persisting a deployment's logs when it terminates.

The capture has to happen before the engine is stopped, and it must never be
able to fail a termination. Both of those are easier to break than to notice,
so they are asserted directly.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.models.model_deployment import deployment_server as ds


class TestAsLogLines:
    """Adapters return either a list of strings or a list of dicts."""

    def test_strings_pass_through(self):
        assert ds._as_log_lines({"logs": ["a", "b"]}) == ["a", "b"]

    def test_dicts_become_json(self):
        got = ds._as_log_lines({"logs": [{"msg": "boom", "level": "error"}]})
        assert json.loads(got[0]) == {"msg": "boom", "level": "error"}

    def test_missing_and_empty_are_handled(self):
        assert ds._as_log_lines(None) == []
        assert ds._as_log_lines({}) == []
        assert ds._as_log_lines({"logs": None}) == []


def _pool(row, instance_id="sts-1"):
    """A db_pool whose acquire() returns a connection with these answers."""
    conn = AsyncMock()
    conn.fetchrow.return_value = row
    conn.fetchval.return_value = instance_id
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _row(node_ids=None, target_node_id="node-1", provider="k8s"):
    return {
        "provider": provider,
        "provider_credential_name": None,
        "node_ids": node_ids,
        "target_node_id": target_node_id,
    }


@pytest.mark.asyncio
class TestCaptureTerminalLogs:

    async def test_returns_the_adapters_lines(self):
        adapter = MagicMock()
        adapter.get_logs = AsyncMock(return_value={"logs": ["line one", "line two"]})

        with patch.object(ds, "get_adapter", return_value=adapter):
            got = await ds._capture_terminal_logs(_pool(_row()), "dep-1")

        assert got == ["line one", "line two"]

    async def test_falls_back_to_the_bound_node_before_running(self):
        """node_ids is only set at RUNNING, so a DEPLOYING deploy has none."""
        adapter = MagicMock()
        adapter.get_logs = AsyncMock(return_value={"logs": ["x"]})
        pool = _pool(_row(node_ids=[], target_node_id="node-7"))

        with patch.object(ds, "get_adapter", return_value=adapter):
            await ds._capture_terminal_logs(pool, "dep-1")

        conn = await pool.acquire.return_value.__aenter__()
        assert conn.fetchval.call_args.args[1] == "node-7"

    async def test_no_node_means_nothing_to_read(self):
        got = await ds._capture_terminal_logs(
            _pool(_row(node_ids=[], target_node_id=None)), "dep-1"
        )
        assert got == []

    async def test_an_adapter_that_raises_does_not_propagate(self):
        """A termination must not fail because the logs could not be read."""
        adapter = MagicMock()
        adapter.get_logs = AsyncMock(side_effect=RuntimeError("cluster unreachable"))

        with patch.object(ds, "get_adapter", return_value=adapter):
            got = await ds._capture_terminal_logs(_pool(_row()), "dep-1")

        assert got == []

    async def test_an_adapter_without_get_logs_is_skipped(self):
        adapter = MagicMock(spec=[])

        with patch.object(ds, "get_adapter", return_value=adapter):
            got = await ds._capture_terminal_logs(_pool(_row()), "dep-1")

        assert got == []

    async def test_a_deployment_with_no_pool_is_skipped(self):
        got = await ds._capture_terminal_logs(_pool(None), "dep-1")
        assert got == []


@pytest.mark.asyncio
async def test_logs_are_read_before_the_engine_is_stopped():
    """unload_model stops the engine and the pod goes with it, so a capture
    that runs afterwards returns nothing."""
    order = []

    conn = AsyncMock()
    conn.fetchval.return_value = None
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)

    db_pool = MagicMock()
    db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    db_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    controller = MagicMock()

    async def _unload(**_):
        order.append("unload")
    controller.unload_model = _unload

    deploys = MagicMock()
    deploys.get = AsyncMock(return_value={
        "state": "RUNNING", "target_node_id": "node-1",
        "gpu_per_replica": 0, "pool_id": None, "org_id": None,
    })
    deploys.update_state_if = AsyncMock(return_value=False)

    deps = MagicMock(
        db_pool=db_pool, controller=controller, inventory=MagicMock(),
        deploys=deploys, pool_repo=MagicMock(get=AsyncMock(return_value=None)),
        jobs_repo=MagicMock(), event_bus=MagicMock(),
    )

    async def _capture(*_a, **_k):
        order.append("capture")
        return ["a line"]

    with patch.object(ds, "_capture_terminal_logs", _capture), \
         patch.object(ds, "log_audit_event", AsyncMock()):
        await ds.terminate_deployment_core("dep-1", deps=deps)

    assert order == ["capture", "unload"], order


@pytest.mark.asyncio
async def test_a_hanging_provider_does_not_stall_the_termination():
    """The Kubernetes read is a blocking call with no timeout of its own, so
    the capture bounds it rather than waiting forever."""
    import asyncio

    adapter = MagicMock()

    async def _never_returns(**_):
        await asyncio.sleep(3600)
    adapter.get_logs = _never_returns

    with patch.object(ds, "get_adapter", return_value=adapter), \
         patch.object(ds, "_TERMINAL_LOG_TIMEOUT_SECONDS", 0.05):
        got = await ds._capture_terminal_logs(_pool(_row()), "dep-1")

    assert got == []


def test_only_the_tail_is_kept():
    """The rows go into the termination transaction, so the list is capped."""
    got = ds._as_log_lines({"logs": [str(i) for i in range(2000)]})
    assert len(got) == ds._TERMINAL_LOG_MAX_LINES
    assert got[-1] == "1999", "the most recent lines are the useful ones"
