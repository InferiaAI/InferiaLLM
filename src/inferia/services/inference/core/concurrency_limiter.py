"""
Concurrency limiter for upstream inference calls.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict, Optional

from cachetools import TTLCache
from fastapi import HTTPException

from inferia.services.inference.config import settings


class UpstreamConcurrencyLimiter:
    """
    Applies optional global and per-deployment in-flight limits to upstream requests.
    """

    def __init__(self):
        self._global_semaphore: Optional[asyncio.Semaphore] = None
        self._global_limit = settings.upstream_global_max_in_flight
        self._per_deployment_limit = settings.upstream_per_deployment_max_in_flight
        self._acquire_timeout_seconds = settings.upstream_slot_acquire_timeout_seconds
        self._deployment_semaphores: TTLCache = TTLCache(maxsize=10000, ttl=300)
        self._lock: Optional[asyncio.Lock] = None

    def _ensure_initialized(self):
        """Create asyncio primitives on first use (after fork)."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        if self._global_semaphore is None and self._global_limit > 0:
            self._global_semaphore = asyncio.Semaphore(self._global_limit)

    async def _get_or_create_deployment_semaphore(
        self, deployment_key: str
    ) -> Optional[asyncio.Semaphore]:
        if self._per_deployment_limit <= 0:
            return None

        async with self._lock:
            if deployment_key not in self._deployment_semaphores:
                self._deployment_semaphores[deployment_key] = asyncio.Semaphore(
                    self._per_deployment_limit
                )
            return self._deployment_semaphores[deployment_key]

    async def _acquire_or_timeout(self, semaphore: asyncio.Semaphore):
        try:
            await asyncio.wait_for(
                semaphore.acquire(), timeout=self._acquire_timeout_seconds
            )
        except asyncio.TimeoutError as e:
            raise HTTPException(
                status_code=429,
                detail="Server is handling too many concurrent requests. Please retry.",
                headers={"Retry-After": "1"},
            ) from e

    @asynccontextmanager
    async def limit(self, deployment_key: str) -> AsyncGenerator[None, None]:
        self._ensure_initialized()
        acquired_global = False
        acquired_deployment = False
        deployment_semaphore: Optional[asyncio.Semaphore] = None

        try:
            if self._global_semaphore is not None:
                await self._acquire_or_timeout(self._global_semaphore)
                acquired_global = True

            deployment_semaphore = await self._get_or_create_deployment_semaphore(
                deployment_key
            )
            if deployment_semaphore is not None:
                await self._acquire_or_timeout(deployment_semaphore)
                acquired_deployment = True

            yield
        finally:
            if acquired_deployment and deployment_semaphore is not None:
                deployment_semaphore.release()
            if acquired_global and self._global_semaphore is not None:
                self._global_semaphore.release()


upstream_concurrency_limiter = UpstreamConcurrencyLimiter()
