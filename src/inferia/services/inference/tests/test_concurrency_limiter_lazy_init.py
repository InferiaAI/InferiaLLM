"""Tests for lazy initialization of asyncio semaphores in UpstreamConcurrencyLimiter.

Issue #46: asyncio.Semaphore created at import time becomes invalid after fork()
in multi-worker mode. Semaphores must be created lazily on first use so each
child process creates them in its own event loop.
"""

import asyncio
import pytest
from unittest.mock import patch

from inferia.services.inference.core.concurrency_limiter import (
    UpstreamConcurrencyLimiter,
)


class TestSemaphoresNotCreatedAtConstruction:
    """Verify that __init__ does NOT create asyncio primitives."""

    def test_global_semaphore_not_created_at_init(self):
        """Global semaphore must be None right after construction."""
        with patch(
            "inferia.services.inference.core.concurrency_limiter.settings"
        ) as mock_settings:
            mock_settings.upstream_global_max_in_flight = 100
            mock_settings.upstream_per_deployment_max_in_flight = 50
            mock_settings.upstream_slot_acquire_timeout_seconds = 20.0
            limiter = UpstreamConcurrencyLimiter()
            assert limiter._global_semaphore is None

    def test_lock_not_created_at_init(self):
        """The internal asyncio.Lock must be None right after construction."""
        with patch(
            "inferia.services.inference.core.concurrency_limiter.settings"
        ) as mock_settings:
            mock_settings.upstream_global_max_in_flight = 100
            mock_settings.upstream_per_deployment_max_in_flight = 50
            mock_settings.upstream_slot_acquire_timeout_seconds = 20.0
            limiter = UpstreamConcurrencyLimiter()
            assert limiter._lock is None

    def test_deployment_semaphores_empty_at_init(self):
        """Per-deployment semaphore dict must be empty after construction."""
        with patch(
            "inferia.services.inference.core.concurrency_limiter.settings"
        ) as mock_settings:
            mock_settings.upstream_global_max_in_flight = 0
            mock_settings.upstream_per_deployment_max_in_flight = 50
            mock_settings.upstream_slot_acquire_timeout_seconds = 20.0
            limiter = UpstreamConcurrencyLimiter()
            assert limiter._deployment_semaphores == {}


class TestSemaphoresCreatedOnFirstUse:
    """Verify semaphores are lazily created when limit() is called."""

    @pytest.mark.asyncio
    async def test_global_semaphore_created_on_acquire(self):
        """Global semaphore must be created on first call to limit()."""
        with patch(
            "inferia.services.inference.core.concurrency_limiter.settings"
        ) as mock_settings:
            mock_settings.upstream_global_max_in_flight = 10
            mock_settings.upstream_per_deployment_max_in_flight = 0
            mock_settings.upstream_slot_acquire_timeout_seconds = 5.0
            limiter = UpstreamConcurrencyLimiter()

            assert limiter._global_semaphore is None
            async with limiter.limit("test-deployment"):
                assert limiter._global_semaphore is not None
                assert isinstance(limiter._global_semaphore, asyncio.Semaphore)

    @pytest.mark.asyncio
    async def test_lock_created_on_acquire(self):
        """Internal lock must be created on first call to limit()."""
        with patch(
            "inferia.services.inference.core.concurrency_limiter.settings"
        ) as mock_settings:
            mock_settings.upstream_global_max_in_flight = 0
            mock_settings.upstream_per_deployment_max_in_flight = 10
            mock_settings.upstream_slot_acquire_timeout_seconds = 5.0
            limiter = UpstreamConcurrencyLimiter()

            assert limiter._lock is None
            async with limiter.limit("test-deployment"):
                assert limiter._lock is not None
                assert isinstance(limiter._lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_deployment_semaphore_created_on_acquire(self):
        """Per-deployment semaphore must be created on first call."""
        with patch(
            "inferia.services.inference.core.concurrency_limiter.settings"
        ) as mock_settings:
            mock_settings.upstream_global_max_in_flight = 0
            mock_settings.upstream_per_deployment_max_in_flight = 10
            mock_settings.upstream_slot_acquire_timeout_seconds = 5.0
            limiter = UpstreamConcurrencyLimiter()

            assert "dep-1" not in limiter._deployment_semaphores
            async with limiter.limit("dep-1"):
                assert "dep-1" in limiter._deployment_semaphores
                assert isinstance(
                    limiter._deployment_semaphores["dep-1"], asyncio.Semaphore
                )

    @pytest.mark.asyncio
    async def test_semaphore_reused_across_calls(self):
        """Subsequent calls must reuse the same semaphore, not create new ones."""
        with patch(
            "inferia.services.inference.core.concurrency_limiter.settings"
        ) as mock_settings:
            mock_settings.upstream_global_max_in_flight = 10
            mock_settings.upstream_per_deployment_max_in_flight = 10
            mock_settings.upstream_slot_acquire_timeout_seconds = 5.0
            limiter = UpstreamConcurrencyLimiter()

            async with limiter.limit("dep-1"):
                first_global = limiter._global_semaphore
                first_dep = limiter._deployment_semaphores["dep-1"]

            async with limiter.limit("dep-1"):
                assert limiter._global_semaphore is first_global
                assert limiter._deployment_semaphores["dep-1"] is first_dep
