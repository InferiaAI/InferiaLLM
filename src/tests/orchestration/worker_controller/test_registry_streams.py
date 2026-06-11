"""Tests for the shell + logs stream multiplexer in WorkerRegistry.

Each fake websocket captures the envelopes the registry sends so we can
assert the wire shape (type, body fields, stream_id round-trip). Streams are
opened via ShellOpenBody/LogsOpenBody using ids that the proxy would have
minted — the registry must register under the body's stream_id verbatim.
"""

import asyncio
import logging

import pytest

from services.orchestration.worker_controller.protocol import (
    LogsEndBody,
    LogsLineBody,
    LogsOpenBody,
    ShellErrorBody,
    ShellExitBody,
    ShellInputBody,
    ShellOpenBody,
    ShellOutputBody,
)
from services.orchestration.worker_controller.registry import (
    StreamHandle,
    WorkerConn,
    WorkerNotConnectedError,
    WorkerRegistry,
)


class FakeWS:
    """Captures send_json payloads. ``fail_next`` triggers one send failure."""

    def __init__(self):
        self.sent: list = []
        self.closed = False
        self.fail_next = False

    async def send_json(self, payload):
        if self.fail_next:
            self.fail_next = False
            raise ConnectionError("simulated ws failure")
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed = True


async def _attach(reg: WorkerRegistry, node_id: str = "node-1") -> FakeWS:
    ws = FakeWS()
    await reg.attach(node_id, WorkerConn(ws=ws, pool_id="p"))
    return ws


# --- Shell happy path -------------------------------------------------------


@pytest.mark.asyncio
async def test_open_shell_stream_registers_and_sends_envelope():
    reg = WorkerRegistry()
    ws = await _attach(reg)

    body = ShellOpenBody(
        stream_id="s-shell-1", shell="/bin/bash", cols=80, rows=24
    )
    handle = await reg.open_shell_stream("node-1", body)

    assert isinstance(handle, StreamHandle)
    assert handle.stream_id == "s-shell-1"
    assert handle.node_id == "node-1"
    assert handle.kind == "shell"
    assert not handle.closed.is_set()

    # Exactly one envelope on the wire: ShellOpen with our body.
    assert len(ws.sent) == 1
    env = ws.sent[0]
    assert env["type"] == "ShellOpen"
    assert env["id"]  # uuid was minted
    assert env["body"]["stream_id"] == "s-shell-1"
    assert env["body"]["shell"] == "/bin/bash"
    assert env["body"]["cols"] == 80


@pytest.mark.asyncio
async def test_shell_happy_path_output_then_exit():
    reg = WorkerRegistry()
    await _attach(reg)

    body = ShellOpenBody(stream_id="s-1")
    handle = await reg.open_shell_stream("node-1", body)

    # Deliver three output chunks then a clean exit.
    reg.deliver_stream_frame(ShellOutputBody(stream_id="s-1", data="hello "))
    reg.deliver_stream_frame(ShellOutputBody(stream_id="s-1", data="world\n"))
    reg.deliver_stream_frame(
        ShellExitBody(stream_id="s-1", exit_code=0, reason="ok")
    )

    # Drain queue.
    f1 = handle.incoming.get_nowait()
    f2 = handle.incoming.get_nowait()
    f3 = handle.incoming.get_nowait()
    assert isinstance(f1, ShellOutputBody) and f1.data == "hello "
    assert isinstance(f2, ShellOutputBody) and f2.data == "world\n"
    assert isinstance(f3, ShellExitBody) and f3.exit_code == 0

    # Exit set the closed event.
    assert handle.closed.is_set()
    # await_close returns immediately.
    await handle.await_close(timeout=0.1)


@pytest.mark.asyncio
async def test_shell_error_also_sets_closed():
    reg = WorkerRegistry()
    await _attach(reg)
    handle = await reg.open_shell_stream("node-1", ShellOpenBody(stream_id="s-e"))

    reg.deliver_stream_frame(
        ShellErrorBody(stream_id="s-e", message="exec failed")
    )
    assert handle.closed.is_set()
    frame = handle.incoming.get_nowait()
    assert isinstance(frame, ShellErrorBody)


# --- Logs happy path --------------------------------------------------------


@pytest.mark.asyncio
async def test_open_logs_stream_and_drain_lines():
    reg = WorkerRegistry()
    ws = await _attach(reg)

    body = LogsOpenBody(stream_id="s-logs-1", deployment_id="dep-42")
    handle = await reg.open_logs_stream("node-1", body)

    assert handle.kind == "logs"
    assert ws.sent[0]["type"] == "LogsOpen"
    assert ws.sent[0]["body"]["deployment_id"] == "dep-42"

    # Push N lines + an end.
    for i in range(5):
        reg.deliver_stream_frame(
            LogsLineBody(stream_id="s-logs-1", data=f"line-{i}", stream="stdout")
        )
    reg.deliver_stream_frame(
        LogsEndBody(stream_id="s-logs-1", reason="container stopped")
    )

    drained = []
    while not handle.incoming.empty():
        drained.append(handle.incoming.get_nowait())
    assert len(drained) == 6
    assert all(isinstance(f, LogsLineBody) for f in drained[:5])
    assert [f.data for f in drained[:5]] == [f"line-{i}" for i in range(5)]
    assert isinstance(drained[5], LogsEndBody)
    assert handle.closed.is_set()


# --- Error paths ------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_shell_against_unconnected_node_raises():
    reg = WorkerRegistry()
    with pytest.raises(WorkerNotConnectedError) as ei:
        await reg.open_shell_stream(
            "ghost", ShellOpenBody(stream_id="s-ghost")
        )
    assert "ghost" in str(ei.value)


@pytest.mark.asyncio
async def test_open_logs_against_unconnected_node_raises():
    reg = WorkerRegistry()
    with pytest.raises(WorkerNotConnectedError):
        await reg.open_logs_stream(
            "ghost", LogsOpenBody(stream_id="s-logs-ghost")
        )


@pytest.mark.asyncio
async def test_open_stream_wire_failure_rolls_back_registration():
    """If the underlying ws.send_json raises, the stream registration is
    rolled back so a retry with the same id succeeds."""
    reg = WorkerRegistry()
    ws = await _attach(reg)
    ws.fail_next = True

    with pytest.raises(ConnectionError):
        await reg.open_shell_stream("node-1", ShellOpenBody(stream_id="s-roll"))

    # Stream was un-registered; we can re-open with the same id.
    handle = await reg.open_shell_stream(
        "node-1", ShellOpenBody(stream_id="s-roll")
    )
    assert handle.stream_id == "s-roll"


@pytest.mark.asyncio
async def test_deliver_stream_frame_unknown_id_is_noop(caplog):
    reg = WorkerRegistry()
    with caplog.at_level(logging.WARNING):
        # Should not raise even though no stream is open.
        reg.deliver_stream_frame(
            ShellOutputBody(stream_id="nobody", data="x")
        )
        reg.deliver_stream_frame(LogsLineBody(stream_id="nobody", data="x"))
    # Both calls should have produced a warning.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) >= 2
    assert all("nobody" in r.getMessage() for r in warnings)


# --- close_stream ----------------------------------------------------------


@pytest.mark.asyncio
async def test_close_shell_stream_sends_shellclose_and_sets_closed():
    reg = WorkerRegistry()
    ws = await _attach(reg)
    handle = await reg.open_shell_stream(
        "node-1", ShellOpenBody(stream_id="s-c")
    )
    # Clear the open envelope from the captured list.
    ws.sent.clear()

    await reg.close_stream("s-c")

    assert handle.closed.is_set()
    assert len(ws.sent) == 1
    assert ws.sent[0]["type"] == "ShellClose"
    assert ws.sent[0]["body"]["stream_id"] == "s-c"


@pytest.mark.asyncio
async def test_close_logs_stream_sends_logsclose():
    reg = WorkerRegistry()
    ws = await _attach(reg)
    await reg.open_logs_stream("node-1", LogsOpenBody(stream_id="s-cl"))
    ws.sent.clear()

    await reg.close_stream("s-cl")

    assert len(ws.sent) == 1
    assert ws.sent[0]["type"] == "LogsClose"
    assert ws.sent[0]["body"]["stream_id"] == "s-cl"


@pytest.mark.asyncio
async def test_close_stream_is_idempotent():
    reg = WorkerRegistry()
    ws = await _attach(reg)
    await reg.open_shell_stream("node-1", ShellOpenBody(stream_id="s-idemp"))
    ws.sent.clear()

    await reg.close_stream("s-idemp")
    await reg.close_stream("s-idemp")  # second call
    await reg.close_stream("never-existed")  # never-opened id

    # Only the first close hit the wire.
    assert len(ws.sent) == 1
    assert ws.sent[0]["type"] == "ShellClose"


@pytest.mark.asyncio
async def test_close_stream_survives_wire_failure():
    """If the worker's ws errors on the close envelope, the local handle is
    still closed (the proxy must be free to clean up regardless)."""
    reg = WorkerRegistry()
    ws = await _attach(reg)
    handle = await reg.open_shell_stream(
        "node-1", ShellOpenBody(stream_id="s-wf")
    )
    ws.fail_next = True

    # Should not raise.
    await reg.close_stream("s-wf")
    assert handle.closed.is_set()


# --- send_shell_input / send_shell_resize ----------------------------------


@pytest.mark.asyncio
async def test_send_shell_input_pushes_envelope():
    reg = WorkerRegistry()
    ws = await _attach(reg)
    await reg.open_shell_stream("node-1", ShellOpenBody(stream_id="s-in"))
    ws.sent.clear()

    await reg.send_shell_input("s-in", "ls -la\n")

    assert len(ws.sent) == 1
    env = ws.sent[0]
    assert env["type"] == "ShellInput"
    assert env["body"]["stream_id"] == "s-in"
    assert env["body"]["data"] == "ls -la\n"
    # Round-trip parse for safety.
    parsed = ShellInputBody(**env["body"])
    assert parsed.data == "ls -la\n"


@pytest.mark.asyncio
async def test_send_shell_resize_pushes_envelope():
    reg = WorkerRegistry()
    ws = await _attach(reg)
    await reg.open_shell_stream("node-1", ShellOpenBody(stream_id="s-rs"))
    ws.sent.clear()

    await reg.send_shell_resize("s-rs", cols=120, rows=40)

    assert len(ws.sent) == 1
    env = ws.sent[0]
    assert env["type"] == "ShellResize"
    assert env["body"]["cols"] == 120
    assert env["body"]["rows"] == 40
    assert env["body"]["stream_id"] == "s-rs"


@pytest.mark.asyncio
async def test_send_shell_input_unknown_stream_is_warning_noop(caplog):
    reg = WorkerRegistry()
    ws = await _attach(reg)
    with caplog.at_level(logging.WARNING):
        await reg.send_shell_input("ghost-stream", "data")
    assert not ws.sent  # nothing on the wire
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("ghost-stream" in m for m in msgs)


@pytest.mark.asyncio
async def test_send_shell_resize_unknown_stream_is_warning_noop(caplog):
    reg = WorkerRegistry()
    ws = await _attach(reg)
    with caplog.at_level(logging.WARNING):
        await reg.send_shell_resize("ghost-stream", 80, 24)
    assert not ws.sent
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("ghost-stream" in m for m in msgs)


@pytest.mark.asyncio
async def test_send_shell_input_disconnected_node_is_warning_noop(caplog):
    """If a stream is registered but its node connection was somehow lost
    without going through detach() (defensive — shouldn't happen in prod),
    sending input is still a warning-level no-op, not an error."""
    reg = WorkerRegistry()
    ws = await _attach(reg, "node-x")
    await reg.open_shell_stream("node-x", ShellOpenBody(stream_id="s-orphan"))
    ws.sent.clear()

    # Forcibly drop the conn without going through detach() (which would
    # also flush the stream).
    reg._conns.pop("node-x")

    with caplog.at_level(logging.WARNING):
        await reg.send_shell_input("s-orphan", "data")
        await reg.send_shell_resize("s-orphan", 80, 24)
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("disconnected" in m for m in msgs)


# --- stream_id uniqueness ---------------------------------------------------


@pytest.mark.asyncio
async def test_two_streams_with_distinct_ids_do_not_collide():
    reg = WorkerRegistry()
    await _attach(reg)
    h1 = await reg.open_shell_stream("node-1", ShellOpenBody(stream_id="s-a"))
    h2 = await reg.open_shell_stream("node-1", ShellOpenBody(stream_id="s-b"))

    assert h1 is not h2
    assert h1.stream_id == "s-a"
    assert h2.stream_id == "s-b"

    # Frames route to the right handle.
    reg.deliver_stream_frame(ShellOutputBody(stream_id="s-a", data="A"))
    reg.deliver_stream_frame(ShellOutputBody(stream_id="s-b", data="B"))
    reg.deliver_stream_frame(ShellOutputBody(stream_id="s-a", data="AA"))

    a_frames = []
    b_frames = []
    while not h1.incoming.empty():
        a_frames.append(h1.incoming.get_nowait())
    while not h2.incoming.empty():
        b_frames.append(h2.incoming.get_nowait())

    assert [f.data for f in a_frames] == ["A", "AA"]
    assert [f.data for f in b_frames] == ["B"]


# --- worker disconnect ------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_disconnect_closes_all_streams():
    """detach() must push a synthetic ShellError and set closed on every
    stream the disconnected node owned. Streams owned by other nodes are
    untouched."""
    reg = WorkerRegistry()
    ws1 = await _attach(reg, "node-1")
    ws2 = await _attach(reg, "node-2")

    s1a = await reg.open_shell_stream("node-1", ShellOpenBody(stream_id="s1a"))
    s1b = await reg.open_logs_stream("node-1", LogsOpenBody(stream_id="s1b"))
    s2a = await reg.open_shell_stream("node-2", ShellOpenBody(stream_id="s2a"))

    # Detach node-1 only.
    await reg.detach("node-1", ws1)

    # Every node-1 stream got the synthetic error + closed event.
    for handle in (s1a, s1b):
        assert handle.closed.is_set()
        frame = handle.incoming.get_nowait()
        assert isinstance(frame, ShellErrorBody)
        assert "worker disconnected" in frame.message
        # Handle is no longer in registry.
        assert reg._streams.get(handle.stream_id) is None

    # node-2's stream is untouched.
    assert not s2a.closed.is_set()
    assert reg._streams.get("s2a") is s2a

    # Now disconnect node-2 as well; same treatment.
    await reg.detach("node-2", ws2)
    assert s2a.closed.is_set()
    err = s2a.incoming.get_nowait()
    assert isinstance(err, ShellErrorBody)


@pytest.mark.asyncio
async def test_stale_detach_does_not_touch_streams():
    """A detach call whose ws does not match the current conn must NOT close
    streams (those belong to the newer conn)."""
    reg = WorkerRegistry()
    ws_old = await _attach(reg, "node-1")
    ws_new = FakeWS()
    await reg.attach("node-1", WorkerConn(ws=ws_new, pool_id="p"))
    # ws_old has been superseded and closed.
    assert ws_old.closed

    handle = await reg.open_shell_stream(
        "node-1", ShellOpenBody(stream_id="s-keep")
    )

    # An old handler firing detach with the stale ws should not flush.
    await reg.detach("node-1", ws_old)

    assert not handle.closed.is_set()
    assert reg._streams.get("s-keep") is handle
    assert reg.get("node-1").ws is ws_new


# --- await_close -----------------------------------------------------------


@pytest.mark.asyncio
async def test_await_close_returns_when_closed():
    reg = WorkerRegistry()
    await _attach(reg)
    handle = await reg.open_shell_stream(
        "node-1", ShellOpenBody(stream_id="s-aw")
    )

    async def _close_soon():
        await asyncio.sleep(0.01)
        reg.deliver_stream_frame(
            ShellExitBody(stream_id="s-aw", exit_code=0)
        )

    asyncio.create_task(_close_soon())
    await handle.await_close(timeout=1.0)
    assert handle.closed.is_set()


@pytest.mark.asyncio
async def test_await_close_times_out_when_stream_alive():
    reg = WorkerRegistry()
    await _attach(reg)
    handle = await reg.open_shell_stream(
        "node-1", ShellOpenBody(stream_id="s-to")
    )
    with pytest.raises(asyncio.TimeoutError):
        await handle.await_close(timeout=0.05)
    assert not handle.closed.is_set()


@pytest.mark.asyncio
async def test_await_close_unbounded_blocks_until_set():
    """``timeout=None`` waits forever; we cancel it manually."""
    reg = WorkerRegistry()
    await _attach(reg)
    handle = await reg.open_shell_stream(
        "node-1", ShellOpenBody(stream_id="s-unb")
    )

    waiter = asyncio.create_task(handle.await_close())
    await asyncio.sleep(0.01)
    assert not waiter.done()

    reg.deliver_stream_frame(ShellExitBody(stream_id="s-unb", exit_code=0))
    await asyncio.wait_for(waiter, timeout=0.5)
    assert handle.closed.is_set()
