"""
In-memory registry mapping node_id → live WebSocket connection.

Also coordinates per-command result futures: when the controller sends a
LoadModel/UnloadModel command and awaits the CommandResult, it parks a Future
here keyed by the command's envelope id; the WS read loop calls
``deliver_command_result`` when a matching reply arrives.

And — for the dashboard's web shell + logs panes — the registry multiplexes
many concurrent long-lived sessions over the same worker→CP control channel.
Each session is identified by a CP-minted ``stream_id`` and represented by a
:class:`StreamHandle`. The proxy (admin_workers.shell/logs HTTP handlers)
opens a stream, drains its ``incoming`` queue into the dashboard WS, and
forwards user input back via ``send_shell_input`` / ``send_shell_resize``.

This module is intentionally small and synchronous-mostly. Persistence of node
state (READY/UNREACHABLE) lives in the inventory_manager and is updated by the
caller — the registry is purely the live-connection cache.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, Union

from .protocol import (
    CommandResultBody,
    Envelope,
    LogsCloseBody,
    LogsEndBody,
    LogsLineBody,
    LogsOpenBody,
    ShellCloseBody,
    ShellErrorBody,
    ShellExitBody,
    ShellInputBody,
    ShellOpenBody,
    ShellOutputBody,
    ShellResizeBody,
)

logger = logging.getLogger(__name__)


StreamKind = Literal["shell", "logs"]

# Body types that flow worker→CP and are routed to a StreamHandle's queue.
StreamFrameBody = Union[
    ShellOutputBody,
    ShellExitBody,
    ShellErrorBody,
    LogsLineBody,
    LogsEndBody,
]


class WorkerNotConnectedError(Exception):
    """Raised when the caller tries to open a stream against a node that has
    no live WS connection. The proxy should translate this to a 503 / closed
    dashboard socket."""


class WebSocketLike(Protocol):
    async def send_json(self, payload: Any) -> None: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


@dataclass
class WorkerConn:
    """One worker's live connection state."""
    ws: WebSocketLike
    pool_id: str
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamHandle:
    """One open shell/logs session multiplexed over a worker's control channel.

    Worker→CP frames for this session land on ``incoming`` (as the parsed body
    object, not the raw envelope — the read loop has already typed it). The
    ``closed`` event fires when a terminal frame (ShellExit/ShellError/LogsEnd)
    arrives OR when :meth:`WorkerRegistry.close_stream` is called OR when the
    worker disconnects entirely.
    """

    stream_id: str
    node_id: str
    kind: StreamKind
    incoming: asyncio.Queue = field(default_factory=asyncio.Queue)
    closed: asyncio.Event = field(default_factory=asyncio.Event)

    async def await_close(self, timeout: float | None = None) -> None:
        """Wait until the stream is closed. Optionally bounded by ``timeout``.

        Raises :class:`asyncio.TimeoutError` if timeout elapses first.
        Passing ``timeout=None`` blocks forever.
        """
        if timeout is None:
            await self.closed.wait()
        else:
            await asyncio.wait_for(self.closed.wait(), timeout=timeout)


class WorkerRegistry:
    def __init__(self):
        self._conns: dict[str, WorkerConn] = {}
        self._pending: dict[str, asyncio.Future] = {}
        # stream_id → StreamHandle. Mutations require self._lock; reads can
        # use dict.get without the lock (atomic in CPython).
        self._streams: dict[str, StreamHandle] = {}
        self._lock = asyncio.Lock()

    async def attach(self, node_id: str, conn: WorkerConn) -> None:
        """Register a new connection for node_id. Closes any existing one."""
        async with self._lock:
            existing = self._conns.get(node_id)
            self._conns[node_id] = conn
        if existing is not None:
            try:
                await existing.ws.close(1000, "superseded by new connection")
            except Exception:
                # Best-effort; another goroutine may have already closed it.
                pass

    async def detach(self, node_id: str, ws: WebSocketLike) -> None:
        """Remove the registry entry only if its ws matches the supplied one.
        This guards against a fast reconnect: handler A sees its peer close and
        tries to detach; meanwhile handler B has already attached a fresh ws.

        When the matching ws is dropped, every open stream owned by ``node_id``
        is closed: each handle gets a synthetic ShellErrorBody pushed onto its
        queue (so the proxy can surface "worker disconnected" to the dashboard)
        and its ``closed`` event is set. Streams are removed from the registry.
        """
        async with self._lock:
            current = self._conns.get(node_id)
            if current is None or current.ws is not ws:
                # Stale detach (superseded by a newer attach). Leave streams
                # alone — they belong to the newer conn.
                return
            self._conns.pop(node_id, None)
            orphaned = [
                handle
                for handle in self._streams.values()
                if handle.node_id == node_id
            ]
            for handle in orphaned:
                self._streams.pop(handle.stream_id, None)

        # Drain the synthetic error + closed signal outside the lock so we
        # never block the lock on queue backpressure (queues are unbounded
        # today but defensive is cheap).
        for handle in orphaned:
            err = ShellErrorBody(
                stream_id=handle.stream_id,
                message="worker disconnected",
            )
            handle.incoming.put_nowait(err)
            handle.closed.set()

    def get(self, node_id: str) -> WorkerConn | None:
        return self._conns.get(node_id)

    def list_nodes(self) -> list[str]:
        return list(self._conns.keys())

    async def send(self, node_id: str, env: Envelope) -> bool:
        conn = self._conns.get(node_id)
        if conn is None:
            return False
        try:
            await conn.ws.send_json(env.model_dump())
        except Exception:
            return False
        return True

    # ------------------------------------------------------------------
    # Command/response correlation.
    # ------------------------------------------------------------------

    def expect_command_result(
        self,
        envelope_id: str,
        timeout: float,
    ) -> asyncio.Future:
        """Return a Future that resolves when a CommandResult with
        in_reply_to == envelope_id arrives, or times out."""
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[envelope_id] = fut

        async def _await():
            try:
                return await asyncio.wait_for(fut, timeout=timeout)
            finally:
                self._pending.pop(envelope_id, None)

        return asyncio.ensure_future(_await())

    def deliver_command_result(self, result: CommandResultBody) -> None:
        fut = self._pending.get(result.in_reply_to)
        if fut is None or fut.done():
            return
        fut.set_result(result)

    # ------------------------------------------------------------------
    # Shell + logs stream multiplexing.
    # ------------------------------------------------------------------

    async def open_shell_stream(
        self, node_id: str, body: ShellOpenBody
    ) -> StreamHandle:
        """Register a fresh shell stream and send ShellOpen to the worker.

        The stream_id on ``body`` is authoritative — it was minted by the
        proxy before this call and is what the dashboard / worker will use
        to route subsequent frames. The registry just records it.
        """
        return await self._open_stream(
            node_id=node_id,
            stream_id=body.stream_id,
            kind="shell",
            envelope_type="ShellOpen",
            body=body,
        )

    async def open_logs_stream(
        self, node_id: str, body: LogsOpenBody
    ) -> StreamHandle:
        """Register a fresh logs stream and send LogsOpen to the worker."""
        return await self._open_stream(
            node_id=node_id,
            stream_id=body.stream_id,
            kind="logs",
            envelope_type="LogsOpen",
            body=body,
        )

    async def _open_stream(
        self,
        *,
        node_id: str,
        stream_id: str,
        kind: StreamKind,
        envelope_type: str,
        body: Any,
    ) -> StreamHandle:
        async with self._lock:
            conn = self._conns.get(node_id)
            if conn is None:
                raise WorkerNotConnectedError(
                    f"no live connection for node_id={node_id}"
                )
            handle = StreamHandle(
                stream_id=stream_id,
                node_id=node_id,
                kind=kind,
            )
            self._streams[stream_id] = handle
            ws = conn.ws

        env = Envelope(
            type=envelope_type,
            id=str(uuid.uuid4()),
            body=body.model_dump(),
        )
        try:
            await ws.send_json(env.model_dump())
        except Exception:
            # Roll back the registration so a retry can use the same id.
            async with self._lock:
                self._streams.pop(stream_id, None)
            raise
        return handle

    async def send_shell_input(self, stream_id: str, data: str) -> None:
        """Forward stdin bytes from the dashboard to the worker.

        No-op (with warning) if the stream isn't open — the dashboard may
        have raced ahead of a worker-side close.
        """
        handle = self._streams.get(stream_id)
        if handle is None:
            logger.warning(
                "send_shell_input on unknown stream_id=%s; dropping", stream_id
            )
            return
        conn = self._conns.get(handle.node_id)
        if conn is None:
            logger.warning(
                "send_shell_input: stream_id=%s belongs to disconnected node=%s",
                stream_id,
                handle.node_id,
            )
            return
        body = ShellInputBody(stream_id=stream_id, data=data)
        env = Envelope(
            type="ShellInput", id=str(uuid.uuid4()), body=body.model_dump()
        )
        await conn.ws.send_json(env.model_dump())

    async def send_shell_resize(
        self, stream_id: str, cols: int, rows: int
    ) -> None:
        """Forward a PTY resize event to the worker."""
        handle = self._streams.get(stream_id)
        if handle is None:
            logger.warning(
                "send_shell_resize on unknown stream_id=%s; dropping", stream_id
            )
            return
        conn = self._conns.get(handle.node_id)
        if conn is None:
            logger.warning(
                "send_shell_resize: stream_id=%s belongs to disconnected node=%s",
                stream_id,
                handle.node_id,
            )
            return
        body = ShellResizeBody(stream_id=stream_id, cols=cols, rows=rows)
        env = Envelope(
            type="ShellResize", id=str(uuid.uuid4()), body=body.model_dump()
        )
        await conn.ws.send_json(env.model_dump())

    def deliver_stream_frame(self, body: StreamFrameBody) -> None:
        """Route a worker→CP stream frame to its StreamHandle.

        Called by the channel read loop after it has parsed the envelope body
        into one of the ShellOutput/ShellExit/ShellError/LogsLine/LogsEnd
        models. Terminal frames (Exit/Error/End) also set the ``closed`` event.
        Unknown stream_ids are dropped with a warning (the worker raced ahead;
        not fatal).
        """
        stream_id = body.stream_id
        handle = self._streams.get(stream_id)
        if handle is None:
            logger.warning(
                "deliver_stream_frame: no handle for stream_id=%s (frame=%s)",
                stream_id,
                type(body).__name__,
            )
            return
        handle.incoming.put_nowait(body)
        if isinstance(body, (ShellExitBody, ShellErrorBody, LogsEndBody)):
            handle.closed.set()

    async def close_stream(self, stream_id: str) -> None:
        """Tear down a stream: send the worker the matching Close envelope,
        flip the ``closed`` event, and forget the handle.

        Idempotent — repeated calls after the first are no-ops.
        """
        async with self._lock:
            handle = self._streams.pop(stream_id, None)
            conn = self._conns.get(handle.node_id) if handle is not None else None

        if handle is None:
            # Already closed (or never opened). Nothing to do.
            return

        # Build the correct close envelope for the stream's kind.
        if handle.kind == "shell":
            close_body = ShellCloseBody(stream_id=stream_id)
            env = Envelope(
                type="ShellClose",
                id=str(uuid.uuid4()),
                body=close_body.model_dump(),
            )
        else:  # "logs"
            close_body = LogsCloseBody(stream_id=stream_id)
            env = Envelope(
                type="LogsClose",
                id=str(uuid.uuid4()),
                body=close_body.model_dump(),
            )

        if conn is not None:
            try:
                await conn.ws.send_json(env.model_dump())
            except Exception:
                # Worker may have already gone away; closing the local handle
                # is still the right thing.
                logger.warning(
                    "close_stream: failed to deliver %s envelope to node=%s",
                    env.type,
                    handle.node_id,
                )
        # Always fire ``closed`` regardless of whether the close envelope
        # delivered — consumers waiting on this are not coupled to the wire.
        handle.closed.set()


__all__ = [
    "WorkerRegistry",
    "WorkerConn",
    "StreamHandle",
    "WorkerNotConnectedError",
]
