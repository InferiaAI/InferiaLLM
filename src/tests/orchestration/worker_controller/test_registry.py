"""Tests for the in-memory worker registry."""

import asyncio

import pytest

from orchestration.workers.worker_controller.registry import (
    METRICS_RING_SIZE,
    WorkerConn,
    WorkerRegistry,
)
from orchestration.workers.worker_controller.protocol import (
    Envelope,
    CommandResultBody,
    ShellOpenBody,
)


class FakeWS:
    """A WebSocket-shaped stub matching the small surface registry uses."""

    def __init__(self):
        self.sent: list = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed = True


@pytest.mark.asyncio
async def test_attach_and_get():
    reg = WorkerRegistry()
    ws = FakeWS()
    await reg.attach("node-1", WorkerConn(ws=ws, pool_id="p"))
    c = reg.get("node-1")
    assert c is not None and c.ws is ws


@pytest.mark.asyncio
async def test_attach_supersedes_existing():
    reg = WorkerRegistry()
    ws1, ws2 = FakeWS(), FakeWS()
    await reg.attach("node-1", WorkerConn(ws=ws1, pool_id="p"))
    await reg.attach("node-1", WorkerConn(ws=ws2, pool_id="p"))
    # First connection closed.
    assert ws1.closed is True
    c = reg.get("node-1")
    assert c.ws is ws2


@pytest.mark.asyncio
async def test_detach_removes_only_matching_conn():
    """detach should be a no-op if another connection has superseded the
    first — prevents races where a quick reconnect arrives before the old
    handler cleans up."""
    reg = WorkerRegistry()
    ws1, ws2 = FakeWS(), FakeWS()
    await reg.attach("node-1", WorkerConn(ws=ws1, pool_id="p"))
    await reg.attach("node-1", WorkerConn(ws=ws2, pool_id="p"))
    # Old handler thinks it should detach ws1; the registry shouldn't drop ws2.
    await reg.detach("node-1", ws1)
    assert reg.get("node-1").ws is ws2

    # Detaching the current one does drop it.
    await reg.detach("node-1", ws2)
    assert reg.get("node-1") is None


@pytest.mark.asyncio
async def test_detach_node_closes_conn_and_removes_it():
    """detach_node forcibly drops a node's live connection by id (the
    reconciler teardown path), closing the ws and removing the entry —
    regardless of which ws is current."""
    reg = WorkerRegistry()
    ws = FakeWS()
    await reg.attach("node-1", WorkerConn(ws=ws, pool_id="p"))
    existed = await reg.detach_node("node-1")
    assert existed is True
    assert ws.closed is True
    assert reg.get("node-1") is None


@pytest.mark.asyncio
async def test_detach_node_unknown_returns_false():
    """detach_node on a node with no live connection is a no-op → False."""
    reg = WorkerRegistry()
    assert await reg.detach_node("ghost") is False


@pytest.mark.asyncio
async def test_detach_node_closes_open_streams():
    """detach_node tears down every open shell/logs stream owned by the node:
    each handle gets a synthetic error frame + its closed event is set."""
    reg = WorkerRegistry()
    ws = FakeWS()
    await reg.attach("node-1", WorkerConn(ws=ws, pool_id="p"))
    handle = await reg.open_shell_stream(
        "node-1", ShellOpenBody(stream_id="s1", cols=80, rows=24),
    )
    assert not handle.closed.is_set()

    await reg.detach_node("node-1")

    assert handle.closed.is_set()
    # A synthetic "worker disconnected" frame was pushed for the proxy.
    frame = handle.incoming.get_nowait()
    assert getattr(frame, "message", "") == "worker disconnected"


@pytest.mark.asyncio
async def test_send_envelope_writes_to_ws():
    reg = WorkerRegistry()
    ws = FakeWS()
    await reg.attach("n", WorkerConn(ws=ws, pool_id="p"))
    ok = await reg.send("n", Envelope(type="Ping", id="x"))
    assert ok is True
    assert ws.sent and ws.sent[0]["type"] == "Ping"


@pytest.mark.asyncio
async def test_send_unknown_node_returns_false():
    reg = WorkerRegistry()
    ok = await reg.send("nope", Envelope(type="Ping", id="x"))
    assert ok is False


@pytest.mark.asyncio
async def test_await_command_result_resolves_on_match():
    reg = WorkerRegistry()
    fut = reg.expect_command_result("cmd-1", timeout=1.0)
    # Deliver matching result.
    reg.deliver_command_result(CommandResultBody(in_reply_to="cmd-1", status="ok"))
    result = await fut
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_await_command_result_times_out():
    reg = WorkerRegistry()
    fut = reg.expect_command_result("cmd-missing", timeout=0.05)
    with pytest.raises(asyncio.TimeoutError):
        await fut


@pytest.mark.asyncio
async def test_deliver_unknown_command_result_ignored():
    """A CommandResult that nobody is awaiting should not raise."""
    reg = WorkerRegistry()
    reg.deliver_command_result(CommandResultBody(in_reply_to="nobody", status="ok"))


@pytest.mark.asyncio
async def test_list_nodes():
    reg = WorkerRegistry()
    assert reg.list_nodes() == []
    await reg.attach("a", WorkerConn(ws=FakeWS(), pool_id="p"))
    await reg.attach("b", WorkerConn(ws=FakeWS(), pool_id="p"))
    nodes = reg.list_nodes()
    assert set(nodes) == {"a", "b"}


# ---------------------------------------------------------------------------
# Metrics ring buffer tests
# ---------------------------------------------------------------------------


class _MetricsFakeWS:
    async def send_json(self, payload): ...
    async def close(self, code: int = 1000, reason: str = ""): ...


def test_record_and_get_metrics_orders_oldest_to_newest():
    reg = WorkerRegistry()
    reg.record_metrics("n1", {"ts": "a"})
    reg.record_metrics("n1", {"ts": "b"})
    samples = reg.get_metrics("n1")
    assert [s["ts"] for s in samples] == ["a", "b"]


def test_get_metrics_unknown_node_is_empty():
    reg = WorkerRegistry()
    assert reg.get_metrics("nope") == []


def test_metrics_ring_trims_to_maxlen():
    reg = WorkerRegistry()
    for i in range(METRICS_RING_SIZE + 50):
        reg.record_metrics("n1", {"ts": str(i)})
    samples = reg.get_metrics("n1")
    assert len(samples) == METRICS_RING_SIZE
    assert samples[0]["ts"] == "50"
    assert samples[-1]["ts"] == str(METRICS_RING_SIZE + 49)


@pytest.mark.asyncio
async def test_detach_drops_metrics_buffer():
    reg = WorkerRegistry()
    ws = _MetricsFakeWS()
    await reg.attach("n1", WorkerConn(ws=ws, pool_id="p"))
    reg.record_metrics("n1", {"ts": "a"})
    await reg.detach("n1", ws)
    assert reg.get_metrics("n1") == []


@pytest.mark.asyncio
async def test_detach_node_drops_metrics_buffer():
    reg = WorkerRegistry()
    ws = _MetricsFakeWS()
    await reg.attach("n1", WorkerConn(ws=ws, pool_id="p"))
    reg.record_metrics("n1", {"ts": "a"})
    await reg.detach_node("n1")
    assert reg.get_metrics("n1") == []
