"""WorkerPool: N async workers all running the same callable in a loop.

Used by the reconciler to run up to N claim-and-dispatch iterations in
parallel. Each worker swallows per-iteration exceptions (logging them)
so one bad job doesn't take down the pool.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable


logger = logging.getLogger(__name__)


class WorkerPool:
    """Run a callable in parallel from up to `concurrency` workers."""

    def __init__(self, *, concurrency: int):
        self.concurrency = concurrency
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    async def start(self, work: Callable[[], Awaitable[None]]) -> None:
        for i in range(self.concurrency):
            t = asyncio.create_task(self._worker(work, i), name=f"worker-{i}")
            self._tasks.append(t)

    async def stop(self) -> None:
        self._stop.set()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _worker(self, work, worker_id: int) -> None:
        while not self._stop.is_set():
            try:
                await work()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "worker %d iteration raised; continuing", worker_id,
                )
            # Tight loops are bad; tiny yield so cancellation lands.
            await asyncio.sleep(0)
