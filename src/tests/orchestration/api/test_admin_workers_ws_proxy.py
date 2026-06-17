"""Tests for the worker logs / shell WS proxies.

The proxies are pure WS multiplexers — every shell or logs session flows
over the existing worker→CP control channel using a CP-minted
``stream_id``. There's no direct TCP dial to the worker's :8080 anymore.

What we lock down here:

* the dashboard wire format (``stdin`` / ``resize`` in; ``output`` /
  ``log`` / ``exit`` / ``error`` out) maps cleanly to the registry's
  ShellInput / ShellResize / ShellOutput / ShellExit / ShellError /
  LogsLine / LogsEnd bodies,
* WorkerNotConnectedError translates to the dashboard-friendly
  ``{"type": "error", "message": "worker offline"}`` envelope and a
  clean close,
* terminal frames (Exit / End / Error) tear the dashboard WS down,
* dashboard disconnect mid-session calls ``close_stream`` so the worker
  gets a matching close envelope (and the registry forgets the handle).

We use a stub registry rather than the real one so we can both inspect
what the proxy *sends* (envelopes for the worker) and *plant* frames
the worker would send back. Real-registry behaviour is covered by
``test_registry_streams.py`` and the channel read loop wiring is
covered by ``test_workers.py``.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
from fastapi import FastAPI

# Repo-wide version skew: starlette 0.35.1 still passes ``app=`` to
# ``httpx.Client``, which httpx 0.28+ removed. Patch the httpx Client
# constructor to drop the ``app`` kwarg for the duration of this test
# module so the TestClient-driven WebSocket fixtures keep working.
import httpx as _httpx
_orig_client_init = _httpx.Client.__init__


def _patched_client_init(self, *args, **kwargs):
    kwargs.pop("app", None)
    return _orig_client_init(self, *args, **kwargs)


_httpx.Client.__init__ = _patched_client_init  # type: ignore[assignment]

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from orchestration.api import admin_workers
from orchestration.workers.worker_controller.protocol import (
    LogsEndBody,
    LogsLineBody,
    LogsOpenBody,
    ShellErrorBody,
    ShellExitBody,
    ShellOpenBody,
    ShellOutputBody,
)
from orchestration.workers.worker_controller.registry import (
    StreamHandle,
    WorkerNotConnectedError,
)


NODE_ID = "11111111-2222-3333-4444-555555555555"


# ---------------------------------------------------------------------------
# Fake registry — observable + scriptable.
# ---------------------------------------------------------------------------


class FakeRegistry:
    """Minimum surface area the proxy uses.

    ``open_shell_stream`` / ``open_logs_stream`` either return a fresh
    ``StreamHandle`` (which the test can push frames onto via
    ``push(stream_id, body)``) or raise ``WorkerNotConnectedError`` when
    ``offline=True``.

    Every CP→worker envelope (input / resize / close) is appended to
    ``self.events`` as a ``(verb, args)`` tuple so the test can assert
    on what the proxy actually sent.
    """

    def __init__(self, *, offline: bool = False):
        self.offline = offline
        self.handles: dict[str, StreamHandle] = {}
        self.opens: list[tuple[str, Any]] = []  # (kind, body)
        self.events: list[tuple[str, tuple]] = []
        self.closed_streams: list[str] = []
        # threading.Events bridge the TestClient's event-loop thread
        # (where the proxy actually runs) with the main test thread.
        self.opened = threading.Event()
        self.event_arrived = threading.Event()
        self.closed_event = threading.Event()
        # The asyncio loop the proxy runs on. Captured during the first
        # open_*_stream call so ``push`` can schedule queue puts on it
        # via call_soon_threadsafe — asyncio.Queue is not threadsafe.
        self._loop: asyncio.AbstractEventLoop | None = None

    async def open_shell_stream(self, node_id: str, body: ShellOpenBody) -> StreamHandle:
        if self.offline:
            raise WorkerNotConnectedError("offline")
        self._loop = asyncio.get_running_loop()
        self.opens.append(("shell", body))
        handle = StreamHandle(
            stream_id=body.stream_id, node_id=node_id, kind="shell",
        )
        self.handles[body.stream_id] = handle
        self.opened.set()
        return handle

    async def open_logs_stream(self, node_id: str, body: LogsOpenBody) -> StreamHandle:
        if self.offline:
            raise WorkerNotConnectedError("offline")
        self._loop = asyncio.get_running_loop()
        self.opens.append(("logs", body))
        handle = StreamHandle(
            stream_id=body.stream_id, node_id=node_id, kind="logs",
        )
        self.handles[body.stream_id] = handle
        self.opened.set()
        return handle

    async def send_shell_input(self, stream_id: str, data: str) -> None:
        self.events.append(("ShellInput", (stream_id, data)))
        self.event_arrived.set()

    async def send_shell_resize(self, stream_id: str, cols: int, rows: int) -> None:
        self.events.append(("ShellResize", (stream_id, cols, rows)))
        self.event_arrived.set()

    async def close_stream(self, stream_id: str) -> None:
        self.closed_streams.append(stream_id)
        handle = self.handles.pop(stream_id, None)
        if handle is not None:
            handle.closed.set()
        self.closed_event.set()

    # Test-side helper: push a frame onto a stream's incoming queue.
    # Schedules the put on the proxy's event loop so the asyncio.Queue's
    # internal futures are touched from the right thread.
    def push(self, stream_id: str, body: Any) -> None:
        handle = self.handles[stream_id]
        loop = self._loop
        if loop is None or not loop.is_running():
            handle.incoming.put_nowait(body)
            return
        loop.call_soon_threadsafe(handle.incoming.put_nowait, body)

    def wait_events(self, count: int, deadline_s: float = 2.0) -> None:
        """Block until at least ``count`` CP→worker events have arrived,
        or fail the test."""
        end = threading.Event()
        # Spin on event_arrived (it's set on every event; reset between
        # waits). Bounded by a deadline computed from wall-clock.
        import time
        stop = time.monotonic() + deadline_s
        while len(self.events) < count and time.monotonic() < stop:
            remaining = max(0.0, stop - time.monotonic())
            self.event_arrived.wait(timeout=remaining)
            self.event_arrived.clear()
        if len(self.events) < count:
            raise AssertionError(
                f"expected {count} events, got {len(self.events)}: {self.events}"
            )

    def wait_closed(self, deadline_s: float = 2.0) -> None:
        """Block until ``close_stream`` has been called at least once."""
        if not self.closed_event.wait(timeout=deadline_s):
            raise AssertionError("close_stream never called within deadline")


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def _make_app(registry: FakeRegistry) -> FastAPI:
    """Build a fresh FastAPI app with admin_workers configured to use the
    supplied registry. The proxy only touches ``worker_registry`` for
    shell/logs — other deps are left as no-op stubs."""
    app = FastAPI()

    admin_workers._deps.worker_auth = None
    admin_workers._deps.worker_registry = registry
    admin_workers._deps.inventory_repo = None
    admin_workers._deps.pool_repo = None
    admin_workers._deps.control_plane_external_url = ""
    admin_workers._deps.require_permission = lambda perm: lambda *_a, **_kw: True
    admin_workers._deps.db_pool = None

    app.include_router(admin_workers.router)
    return app


@pytest.fixture(autouse=True)
def reset_deps():
    """Snapshot and restore the module-level deps singleton around each test."""
    saved = (
        admin_workers._deps.worker_auth,
        admin_workers._deps.worker_registry,
        admin_workers._deps.inventory_repo,
        admin_workers._deps.pool_repo,
        admin_workers._deps.control_plane_external_url,
        admin_workers._deps.require_permission,
        admin_workers._deps.db_pool,
    )
    yield
    (
        admin_workers._deps.worker_auth,
        admin_workers._deps.worker_registry,
        admin_workers._deps.inventory_repo,
        admin_workers._deps.pool_repo,
        admin_workers._deps.control_plane_external_url,
        admin_workers._deps.require_permission,
        admin_workers._deps.db_pool,
    ) = saved


def _stream_id(registry: FakeRegistry, deadline_s: float = 2.0) -> str:
    """Return the stream_id the proxy minted for the open call.

    The proxy's call to ``open_*_stream`` happens on the TestClient's
    background event-loop thread; the test body runs on the main
    thread. Block on the registry's ``opened`` event so we don't race
    the server handler — and don't poll, which would either spin-burn
    a CPU or require a sleep.
    """
    if not registry.opened.wait(timeout=deadline_s):
        raise AssertionError("proxy never called open_*_stream within deadline")
    _kind, body = registry.opens[0]
    return body.stream_id


# ---------------------------------------------------------------------------
# Shell happy path.
# ---------------------------------------------------------------------------


def test_shell_open_passes_query_params_into_body():
    """Every query param the dashboard sends must round-trip into
    ShellOpenBody so the worker exec's the right command in the right
    container with the right PTY size."""
    reg = FakeRegistry()
    app = _make_app(reg)

    qs = "deployment=dep-1&container=ctr-2&shell=/bin/bash&user=root&cols=120&rows=40"
    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/shell?{qs}",
    ) as ws:
        # Push an exit to terminate the worker_to_dashboard loop and let the
        # context manager close cleanly.
        sid = _stream_id(reg)
        reg.push(sid, ShellExitBody(stream_id=sid, exit_code=0))
        msg = ws.receive_json()
        assert msg == {"type": "exit", "exit_code": 0, "reason": ""}

    assert len(reg.opens) == 1
    kind, body = reg.opens[0]
    assert kind == "shell"
    assert isinstance(body, ShellOpenBody)
    assert body.deployment_id == "dep-1"
    assert body.container_id == "ctr-2"
    assert body.shell == "/bin/bash"
    assert body.user == "root"
    assert body.cols == 120
    assert body.rows == 40
    # The proxy must always tear the registry stream down on exit.
    assert reg.closed_streams == [body.stream_id]


def test_shell_stdin_translates_to_shell_input():
    """A ``{type: stdin}`` frame from the dashboard becomes a ShellInput
    envelope on the worker control channel."""
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/shell?shell=/bin/sh",
    ) as ws:
        ws.send_json({"type": "stdin", "data": "ls -la\n"})
        ws.send_json({"type": "stdin", "data": "\x03"})  # Ctrl-C
        # Block until both events have been routed through the proxy to
        # the registry on the server's event loop. Without this we race
        # the dashboard→worker drain.
        reg.wait_events(2)
        sid = _stream_id(reg)
        reg.push(sid, ShellExitBody(stream_id=sid))
        ws.receive_json()

    # The two stdin frames must have produced ShellInput events in order.
    inputs = [e for e in reg.events if e[0] == "ShellInput"]
    assert len(inputs) == 2
    assert inputs[0][1][1] == "ls -la\n"
    assert inputs[1][1][1] == "\x03"


def test_shell_output_translates_to_dashboard_wire_format():
    """ShellOutput bodies from the worker become ``{type: output}`` JSON
    on the dashboard side, exactly as the React client expects."""
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/shell",
    ) as ws:
        sid = _stream_id(reg)
        reg.push(sid, ShellOutputBody(stream_id=sid, data="hello\n"))
        reg.push(sid, ShellOutputBody(stream_id=sid, data="world\n"))
        reg.push(sid, ShellExitBody(stream_id=sid, exit_code=0, reason="done"))

        assert ws.receive_json() == {"type": "output", "data": "hello\n"}
        assert ws.receive_json() == {"type": "output", "data": "world\n"}
        assert ws.receive_json() == {
            "type": "exit", "exit_code": 0, "reason": "done",
        }


def test_shell_resize_forwarded_as_shell_resize_envelope():
    """A ``{type: resize, rows, cols}`` frame becomes a ShellResize
    envelope. PTY size lives on the worker side."""
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/shell",
    ) as ws:
        ws.send_json({"type": "resize", "rows": 50, "cols": 132})
        reg.wait_events(1)
        sid = _stream_id(reg)
        reg.push(sid, ShellExitBody(stream_id=sid))
        ws.receive_json()

    resizes = [e for e in reg.events if e[0] == "ShellResize"]
    assert len(resizes) == 1
    # Signature: (stream_id, cols, rows)
    assert resizes[0][1][1] == 132
    assert resizes[0][1][2] == 50


def test_shell_error_frame_closes_dashboard_with_error_envelope():
    """A ShellError from the worker translates to ``{type: error}`` and
    terminates the worker→dashboard pump."""
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/shell",
    ) as ws:
        sid = _stream_id(reg)
        reg.push(sid, ShellErrorBody(stream_id=sid, message="exec: no such file"))
        msg = ws.receive_json()
        assert msg == {"type": "error", "message": "exec: no such file"}
        # After the terminal error frame the next receive on the dashboard
        # side should observe the server closing the WS.
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()

    reg.wait_closed()
    assert reg.closed_streams == [sid]


def test_shell_worker_offline_reports_clean_error():
    """Opening against a node with no live worker WS must surface a
    structured error JSON, then close the dashboard WS — never a raw
    crash or a hanging socket."""
    reg = FakeRegistry(offline=True)
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/shell",
    ) as ws:
        msg = ws.receive_json()
        assert msg == {"type": "error", "message": "worker offline"}
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()

    # No stream to close (open failed); event list must be empty too.
    assert reg.closed_streams == []
    assert reg.events == []
    assert reg.opens == []


def test_shell_dashboard_disconnect_closes_stream():
    """If the dashboard hangs up mid-session, the proxy must call
    ``close_stream`` so the worker tears down its end."""
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/shell",
    ) as ws:
        sid = _stream_id(reg)
        # Send one input so the dashboard→worker pump is definitely running.
        ws.send_json({"type": "stdin", "data": "echo hi\n"})
        reg.wait_events(1)
        # Now drop the WS without an exit frame.
        ws.close()

    # After context exit, the proxy's finally block runs close_stream
    # on the server thread. Block until that happens.
    reg.wait_closed()
    assert reg.closed_streams == [sid]


def test_shell_resize_missing_fields_safely_dropped():
    """A malformed resize (missing rows/cols, or non-int) must not crash
    the proxy — it should just be silently ignored."""
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/shell",
    ) as ws:
        ws.send_json({"type": "resize"})  # nothing
        ws.send_json({"type": "resize", "rows": "wat", "cols": "??"})
        # Send a valid one so we know the pump kept running.
        ws.send_json({"type": "resize", "rows": 10, "cols": 20})
        # The missing-fields frame yields a (0, 0) ShellResize; the
        # non-int frame is dropped before reaching the registry; the
        # third produces (20, 10). So we expect exactly 2 events.
        reg.wait_events(2)
        sid = _stream_id(reg)
        reg.push(sid, ShellExitBody(stream_id=sid))
        ws.receive_json()

    resizes = [e for e in reg.events if e[0] == "ShellResize"]
    assert (0, 0) in [(c, r) for (_sid, c, r) in (e[1] for e in resizes)]
    assert (20, 10) in [(c, r) for (_sid, c, r) in (e[1] for e in resizes)]
    # Exactly two valid resizes — the non-int variant was dropped.
    assert len(resizes) == 2


def test_shell_stdin_non_string_data_dropped():
    """Defensive: a stdin frame whose ``data`` is not a string must be
    ignored, not forwarded as garbage."""
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/shell",
    ) as ws:
        ws.send_json({"type": "stdin", "data": 12345})  # number, not str
        ws.send_json({"type": "stdin", "data": "ok\n"})
        reg.wait_events(1)
        sid = _stream_id(reg)
        reg.push(sid, ShellExitBody(stream_id=sid))
        ws.receive_json()

    inputs = [e for e in reg.events if e[0] == "ShellInput"]
    assert len(inputs) == 1
    assert inputs[0][1][1] == "ok\n"


def test_shell_unknown_frame_type_ignored():
    """Frames the dashboard doesn't know about (or future extensions)
    must round-trip silently — no crash, no spurious event."""
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/shell",
    ) as ws:
        ws.send_json({"type": "ping"})  # not in our schema
        ws.send_json({"type": "stdin", "data": "x"})
        reg.wait_events(1)
        sid = _stream_id(reg)
        reg.push(sid, ShellExitBody(stream_id=sid))
        ws.receive_json()

    inputs = [e for e in reg.events if e[0] == "ShellInput"]
    assert len(inputs) == 1
    assert inputs[0][1][1] == "x"


def test_shell_default_cols_rows_when_missing():
    """If the dashboard omits cols/rows on the open URL, the body must
    use the defaults baked into ShellOpenBody (0/0 → worker keeps the
    PTY at its default)."""
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/shell",
    ) as ws:
        sid = _stream_id(reg)
        reg.push(sid, ShellExitBody(stream_id=sid))
        ws.receive_json()

    _, body = reg.opens[0]
    assert body.cols == 0
    assert body.rows == 0
    assert body.shell == "/bin/sh"  # default
    assert body.user == ""


# ---------------------------------------------------------------------------
# Logs happy path.
# ---------------------------------------------------------------------------


def test_logs_open_passes_deployment_and_container_into_body():
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/logs?deployment=dep-7&container=ctr-3",
    ) as ws:
        sid = _stream_id(reg)
        reg.push(sid, LogsEndBody(stream_id=sid, reason="container exited"))
        msg = ws.receive_json()
        assert msg == {"type": "exit", "reason": "container exited"}

    kind, body = reg.opens[0]
    assert kind == "logs"
    assert isinstance(body, LogsOpenBody)
    assert body.deployment_id == "dep-7"
    assert body.container_id == "ctr-3"
    assert reg.closed_streams == [body.stream_id]


def test_logs_multiple_lines_arrive_in_order():
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/logs",
    ) as ws:
        sid = _stream_id(reg)
        reg.push(sid, LogsLineBody(stream_id=sid, stream="stdout", data="line1"))
        reg.push(sid, LogsLineBody(stream_id=sid, stream="stderr", data="oops"))
        reg.push(sid, LogsLineBody(stream_id=sid, stream="stdout", data="line2"))
        reg.push(sid, LogsEndBody(stream_id=sid, reason="eof"))

        assert ws.receive_json() == {
            "type": "log", "stream": "stdout", "data": "line1",
        }
        assert ws.receive_json() == {
            "type": "log", "stream": "stderr", "data": "oops",
        }
        assert ws.receive_json() == {
            "type": "log", "stream": "stdout", "data": "line2",
        }
        assert ws.receive_json() == {"type": "exit", "reason": "eof"}


def test_logs_worker_offline_reports_clean_error():
    reg = FakeRegistry(offline=True)
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/logs",
    ) as ws:
        msg = ws.receive_json()
        assert msg == {"type": "error", "message": "worker offline"}
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()

    assert reg.opens == []
    assert reg.closed_streams == []


def test_logs_worker_disconnect_surfaces_as_error():
    """When the worker disappears mid-stream, the registry synthesises a
    ShellErrorBody on the queue (regardless of stream kind). The logs
    proxy must surface that as an ``error`` envelope, not silently ignore
    it."""
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/logs",
    ) as ws:
        sid = _stream_id(reg)
        reg.push(sid, ShellErrorBody(
            stream_id=sid, message="worker disconnected",
        ))
        assert ws.receive_json() == {
            "type": "error", "message": "worker disconnected",
        }
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_logs_dashboard_disconnect_closes_stream():
    """Same teardown contract as shell: the proxy must call
    ``close_stream`` when the dashboard hangs up."""
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/logs",
    ) as ws:
        sid = _stream_id(reg)
        ws.close()

    reg.wait_closed()
    assert reg.closed_streams == [sid]


def test_logs_empty_query_params_yield_blank_targets():
    """Omitting both deployment and container is legal — the worker
    falls back to its first running container. The proxy must accept
    that without 400-ing."""
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/logs",
    ) as ws:
        sid = _stream_id(reg)
        reg.push(sid, LogsEndBody(stream_id=sid))
        ws.receive_json()

    _, body = reg.opens[0]
    assert body.deployment_id == ""
    assert body.container_id == ""


# ---------------------------------------------------------------------------
# Defensive paths — unexpected frame types and malformed query params.
# Coverage-oriented; production should never hit these.
# ---------------------------------------------------------------------------


def test_shell_logs_unexpected_frame_logged_and_ignored():
    """If a non-shell frame somehow lands on a shell stream's queue (e.g.
    a LogsLine due to mis-routing) it must not crash the proxy. The
    worker→dashboard pump logs and keeps draining."""
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/shell",
    ) as ws:
        sid = _stream_id(reg)
        # Push a LogsLine onto a shell stream — not part of the shell's
        # wire vocabulary. The proxy should warn and continue.
        reg.push(sid, LogsLineBody(stream_id=sid, data="garbage"))
        reg.push(sid, ShellExitBody(stream_id=sid, exit_code=0))
        msg = ws.receive_json()
        # The first message must be the exit (the LogsLine is dropped).
        assert msg == {"type": "exit", "exit_code": 0, "reason": ""}


def test_logs_unexpected_frame_logged_and_ignored():
    """Same defence on the logs proxy — a ShellOutput on a logs stream
    is mis-routing; we log and keep draining until LogsEnd / disconnect."""
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/logs",
    ) as ws:
        sid = _stream_id(reg)
        reg.push(sid, ShellOutputBody(stream_id=sid, data="not a log line"))
        reg.push(sid, LogsEndBody(stream_id=sid, reason="eof"))
        msg = ws.receive_json()
        assert msg == {"type": "exit", "reason": "eof"}


# ---------------------------------------------------------------------------
# DePIN shell gate — Nosana/Akash nodes have no worker channel / exec, so the
# shell must degrade with a CLEAR message + normal close, never the misleading
# "worker offline" (and never reach the registry).
# ---------------------------------------------------------------------------


class _FakeInventory:
    def __init__(self, node):
        self._node = node

    async def get_node_by_id(self, node_id):
        return self._node


def test_shell_depin_node_gated_with_clear_message():
    reg = FakeRegistry()
    app = _make_app(reg)
    admin_workers._deps.inventory_repo = _FakeInventory({"provider": "nosana"})

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/shell",
    ) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "DePIN" in msg["message"]
        assert "Logs tab" in msg["message"]
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()

    # The gate fires BEFORE opening any worker stream.
    assert reg.opens == []
    assert reg.closed_streams == []


def test_shell_worker_node_not_gated():
    """A worker-provider node must still reach the worker shell stream — the
    DePIN gate must not fire for it."""
    reg = FakeRegistry()
    app = _make_app(reg)
    admin_workers._deps.inventory_repo = _FakeInventory({"provider": "worker"})

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/shell",
    ) as ws:
        sid = _stream_id(reg)
        reg.push(sid, ShellExitBody(stream_id=sid))
        ws.receive_json()

    assert len(reg.opens) == 1  # reached the worker shell path


def test_shell_malformed_cols_query_param_falls_back_to_default():
    """The dashboard could theoretically send ``cols=lol``; the proxy
    must coerce to the default rather than 500ing on URL parse."""
    reg = FakeRegistry()
    app = _make_app(reg)

    with TestClient(app).websocket_connect(
        f"/v1/admin/workers/{NODE_ID}/shell?cols=lol&rows=&shell=/bin/sh",
    ) as ws:
        sid = _stream_id(reg)
        reg.push(sid, ShellExitBody(stream_id=sid))
        ws.receive_json()

    _, body = reg.opens[0]
    assert body.cols == 0  # fallback
    assert body.rows == 0  # empty string also falls back
