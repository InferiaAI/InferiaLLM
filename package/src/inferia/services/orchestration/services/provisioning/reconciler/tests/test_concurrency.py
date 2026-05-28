"""Tests for the WorkerPool that processes claimed jobs in parallel."""
from __future__ import annotations

import asyncio

import pytest

from inferia.services.orchestration.services.provisioning.reconciler.concurrency import (
    WorkerPool,
)


@pytest.mark.asyncio
async def test_worker_pool_runs_callable_with_target_concurrency():
    active = 0
    peak = 0

    async def work():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1

    pool = WorkerPool(concurrency=4)
    await pool.start(work)
    await asyncio.sleep(0.2)
    await pool.stop()

    assert peak == 4, f"expected concurrency=4, peak={peak}"


@pytest.mark.asyncio
async def test_worker_pool_stop_drains():
    """stop() waits for in-flight work to complete."""
    completed = 0

    async def work():
        nonlocal completed
        await asyncio.sleep(0.05)
        completed += 1

    pool = WorkerPool(concurrency=2)
    await pool.start(work)
    await asyncio.sleep(0.02)
    await pool.stop()
    # At least the started ones should have finished.
    assert completed >= 2


@pytest.mark.asyncio
async def test_worker_pool_swallows_per_task_exceptions():
    """A single iteration raising doesn't kill the pool."""
    iterations = 0

    async def work():
        nonlocal iterations
        iterations += 1
        if iterations == 1:
            raise RuntimeError("first iteration boom")

    pool = WorkerPool(concurrency=2)
    await pool.start(work)
    await asyncio.sleep(0.05)
    await pool.stop()
    assert iterations >= 2
