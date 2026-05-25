"""Thread-safe bridge between Pulumi's synchronous on_event callback
and the asyncio repo. Captures the running loop at construction; the
sync `write()` method schedules a coroutine via
`asyncio.run_coroutine_threadsafe`, the async `write_async()` method
awaits inline.

Errors from the underlying repo are swallowed in the sync path so a
write failure cannot crash the Pulumi up() thread.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)

_MAX_MESSAGE_BYTES = 1024


def _truncate(msg: Optional[str]) -> Optional[str]:
    if msg is None:
        return None
    if len(msg) <= _MAX_MESSAGE_BYTES:
        return msg
    return msg[:_MAX_MESSAGE_BYTES]


class ProgressWriter:
    def __init__(
        self,
        repo,
        *,
        pool_id: UUID,
        node_id: Optional[UUID],
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        self._repo = repo
        self._pool_id = pool_id
        self._node_id = node_id
        try:
            self._loop = loop or asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    async def write_async(
        self, phase: str, status: str, message: Optional[str] = None,
    ) -> None:
        await self._repo.append_event(
            pool_id=self._pool_id,
            node_id=self._node_id,
            phase=phase,
            status=status,
            message=_truncate(message),
        )

    def write(self, phase: str, status: str, message: Optional[str] = None) -> None:
        """Synchronous write — safe to call from the Pulumi thread."""
        if self._loop is None:
            logger.warning("progress writer has no loop; dropping event %s/%s",
                           phase, status)
            return
        async def _run():
            try:
                await self.write_async(phase, status, message)
            except Exception as e:
                logger.warning("progress event write failed: %s", e)
        try:
            asyncio.run_coroutine_threadsafe(_run(), self._loop)
        except Exception as e:
            logger.warning("could not schedule progress event: %s", e)
