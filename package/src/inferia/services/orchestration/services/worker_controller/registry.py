"""
In-memory registry mapping node_id → live WebSocket connection.

Also coordinates per-command result futures: when the controller sends a
LoadModel/UnloadModel command and awaits the CommandResult, it parks a Future
here keyed by the command's envelope id; the WS read loop calls
``deliver_command_result`` when a matching reply arrives.

This module is intentionally small and synchronous-mostly. Persistence of node
state (READY/UNREACHABLE) lives in the inventory_manager and is updated by the
caller — the registry is purely the live-connection cache.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

from .protocol import CommandResultBody, Envelope


class WebSocketLike(Protocol):
    async def send_json(self, payload: Any) -> None: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


@dataclass
class WorkerConn:
    """One worker's live connection state."""
    ws: WebSocketLike
    pool_id: str
    extras: dict[str, Any] = field(default_factory=dict)


class WorkerRegistry:
    def __init__(self):
        self._conns: dict[str, WorkerConn] = {}
        self._pending: dict[str, asyncio.Future] = {}
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
        tries to detach; meanwhile handler B has already attached a fresh ws."""
        async with self._lock:
            current = self._conns.get(node_id)
            if current is not None and current.ws is ws:
                self._conns.pop(node_id, None)

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


__all__ = ["WorkerRegistry", "WorkerConn"]
