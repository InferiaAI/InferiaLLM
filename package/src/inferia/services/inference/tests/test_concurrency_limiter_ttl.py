"""Tests for concurrency limiter TTLCache and streaming slot release (#72, #75)."""

import asyncio
import pytest
from unittest.mock import patch, MagicMock
from cachetools import TTLCache

from inferia.services.inference.core.concurrency_limiter import UpstreamConcurrencyLimiter


class TestConcurrencyLimiterTTLCache:
    def test_deployment_semaphores_is_ttl_cache(self):
        """_deployment_semaphores must be a TTLCache, not a plain dict."""
        limiter = UpstreamConcurrencyLimiter()
        assert isinstance(limiter._deployment_semaphores, TTLCache)

    def test_deployment_semaphores_bounded(self):
        limiter = UpstreamConcurrencyLimiter()
        assert limiter._deployment_semaphores.maxsize > 0

    def test_deployment_semaphores_has_ttl(self):
        limiter = UpstreamConcurrencyLimiter()
        assert limiter._deployment_semaphores.ttl > 0

    @pytest.mark.asyncio
    async def test_get_or_create_creates_semaphore(self):
        limiter = UpstreamConcurrencyLimiter()
        limiter._ensure_initialized()
        # Need per-deployment limit > 0
        limiter._per_deployment_limit = 10

        sem = await limiter._get_or_create_deployment_semaphore("dep-1")
        assert isinstance(sem, asyncio.Semaphore)
        assert "dep-1" in limiter._deployment_semaphores

    @pytest.mark.asyncio
    async def test_get_or_create_returns_same_semaphore(self):
        limiter = UpstreamConcurrencyLimiter()
        limiter._ensure_initialized()
        limiter._per_deployment_limit = 10

        s1 = await limiter._get_or_create_deployment_semaphore("dep-2")
        s2 = await limiter._get_or_create_deployment_semaphore("dep-2")
        assert s1 is s2

    @pytest.mark.asyncio
    async def test_limit_context_manager_acquires_and_releases(self):
        limiter = UpstreamConcurrencyLimiter()
        limiter._global_limit = 2
        limiter._per_deployment_limit = 0
        limiter._acquire_timeout_seconds = 5

        async with limiter.limit("dep-test"):
            pass  # should not raise

    @pytest.mark.asyncio
    async def test_limit_raises_429_on_timeout(self):
        from fastapi import HTTPException

        limiter = UpstreamConcurrencyLimiter()
        limiter._global_limit = 1
        limiter._per_deployment_limit = 0
        limiter._acquire_timeout_seconds = 0.01

        # Acquire the only slot
        limiter._ensure_initialized()
        await limiter._global_semaphore.acquire()

        with pytest.raises(HTTPException) as exc:
            async with limiter.limit("dep-x"):
                pass

        assert exc.value.status_code == 429

        # Cleanup
        limiter._global_semaphore.release()

    @pytest.mark.asyncio
    async def test_ensure_initialized_idempotent(self):
        limiter = UpstreamConcurrencyLimiter()
        limiter._global_limit = 5
        limiter._ensure_initialized()
        lock1 = limiter._lock
        sem1 = limiter._global_semaphore
        limiter._ensure_initialized()
        assert limiter._lock is lock1
        assert limiter._global_semaphore is sem1

    @pytest.mark.asyncio
    async def test_no_global_semaphore_when_limit_zero(self):
        limiter = UpstreamConcurrencyLimiter()
        limiter._global_limit = 0
        limiter._ensure_initialized()
        assert limiter._global_semaphore is None

    @pytest.mark.asyncio
    async def test_no_deployment_semaphore_when_limit_zero(self):
        limiter = UpstreamConcurrencyLimiter()
        limiter._ensure_initialized()
        limiter._per_deployment_limit = 0
        sem = await limiter._get_or_create_deployment_semaphore("dep")
        assert sem is None

    @pytest.mark.asyncio
    async def test_failed_request_releases_slots(self):
        """If an exception occurs inside `limit()`, slots must still be released."""
        limiter = UpstreamConcurrencyLimiter()
        limiter._global_limit = 1
        limiter._per_deployment_limit = 0
        limiter._acquire_timeout_seconds = 5

        try:
            async with limiter.limit("dep-err"):
                raise ValueError("boom")
        except ValueError:
            pass

        # Global semaphore should be available again
        limiter._ensure_initialized()
        # Can acquire without blocking
        acquired = limiter._global_semaphore._value >= 1
        assert acquired
