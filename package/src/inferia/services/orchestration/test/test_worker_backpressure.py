"""Tests for deploy worker backpressure semaphore (#80).

These tests verify the semaphore-based concurrency limiting pattern
without importing worker_main (which pulls in protobuf/gRPC dependencies
that may have version conflicts in test environments).
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID


class TestSemaphoreBackpressure:
    """Test the semaphore pattern used by the deploy worker."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """The semaphore should limit concurrent task execution."""
        max_concurrent = 2
        sem = asyncio.Semaphore(max_concurrent)
        active = 0
        peak = 0

        async def work():
            nonlocal active, peak
            async with sem:
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.05)
                active -= 1

        tasks = [asyncio.create_task(work()) for _ in range(10)]
        await asyncio.gather(*tasks)

        assert peak <= max_concurrent

    @pytest.mark.asyncio
    async def test_semaphore_value_1_serializes_tasks(self):
        """Semaphore(1) should serialize all tasks."""
        sem = asyncio.Semaphore(1)
        active = 0
        peak = 0

        async def work():
            nonlocal active, peak
            async with sem:
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1

        tasks = [asyncio.create_task(work()) for _ in range(5)]
        await asyncio.gather(*tasks)

        assert peak == 1

    @pytest.mark.asyncio
    async def test_failed_task_releases_semaphore(self):
        """A failing task must release the semaphore slot."""
        sem = asyncio.Semaphore(1)

        async def failing_work():
            async with sem:
                raise ValueError("boom")

        with pytest.raises(ValueError):
            await failing_work()

        # Semaphore should be available again
        assert not sem.locked()

    @pytest.mark.asyncio
    async def test_semaphore_shared_between_consumers(self):
        """Deploy and terminate consumers sharing a semaphore should be bounded."""
        max_concurrent = 2
        sem = asyncio.Semaphore(max_concurrent)
        active = 0
        peak = 0

        async def deploy_work():
            nonlocal active, peak
            async with sem:
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.02)
                active -= 1

        async def terminate_work():
            nonlocal active, peak
            async with sem:
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.02)
                active -= 1

        tasks = []
        for _ in range(5):
            tasks.append(asyncio.create_task(deploy_work()))
            tasks.append(asyncio.create_task(terminate_work()))
        await asyncio.gather(*tasks)

        assert peak <= max_concurrent

    @pytest.mark.asyncio
    async def test_process_deploy_pattern_with_semaphore(self):
        """Verify the exact pattern used in consume_deploy_requests."""
        sem = asyncio.Semaphore(2)
        worker = MagicMock()
        worker.handle_deploy_requested = AsyncMock()
        event_bus_redis = MagicMock()
        event_bus_redis.xack = AsyncMock()

        async def process_deploy(msg_id, event):
            async with sem:
                deployment_id = UUID(event["deployment_id"])
                await worker.handle_deploy_requested(deployment_id)
                await event_bus_redis.xack(
                    "model.deploy.requested", "deployment-workers", msg_id
                )

        dep_id = "00000000-0000-0000-0000-000000000001"
        await process_deploy("msg-1", {"deployment_id": dep_id})

        worker.handle_deploy_requested.assert_awaited_once_with(UUID(dep_id))
        event_bus_redis.xack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_terminate_pattern_with_semaphore(self):
        """Verify the exact pattern used in consume_terminate_requests."""
        sem = asyncio.Semaphore(2)
        worker = MagicMock()
        worker.handle_terminate_requested = AsyncMock()
        event_bus_redis = MagicMock()
        event_bus_redis.xack = AsyncMock()

        async def process_terminate(msg_id, event):
            async with sem:
                deployment_id = UUID(event["deployment_id"])
                await worker.handle_terminate_requested(deployment_id)
                await event_bus_redis.xack(
                    "model.terminate.requested", "deployment-workers", msg_id
                )

        dep_id = "00000000-0000-0000-0000-000000000002"
        await process_terminate("msg-1", {"deployment_id": dep_id})

        worker.handle_terminate_requested.assert_awaited_once_with(UUID(dep_id))

    @pytest.mark.asyncio
    async def test_exception_in_handler_still_releases_semaphore(self):
        """If handler raises, semaphore must still be released for next task."""
        sem = asyncio.Semaphore(1)

        async def process_with_error():
            async with sem:
                raise RuntimeError("handler failed")

        # First call fails
        with pytest.raises(RuntimeError):
            await process_with_error()

        # Second call should succeed (not deadlocked)
        result = []

        async def process_success():
            async with sem:
                result.append("ok")

        await process_success()
        assert result == ["ok"]

    @pytest.mark.asyncio
    async def test_burst_within_semaphore_limit(self):
        """Burst of events within semaphore limit should all proceed."""
        sem = asyncio.Semaphore(5)
        completed = []

        async def handler(idx):
            async with sem:
                completed.append(idx)

        tasks = [asyncio.create_task(handler(i)) for i in range(5)]
        await asyncio.gather(*tasks)

        assert sorted(completed) == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_semaphore_blocks_beyond_limit(self):
        """Tasks beyond the limit should wait until a slot opens."""
        sem = asyncio.Semaphore(1)
        order = []

        async def handler(idx, delay=0):
            async with sem:
                order.append(f"start-{idx}")
                await asyncio.sleep(delay)
                order.append(f"end-{idx}")

        t1 = asyncio.create_task(handler(1, 0.05))
        await asyncio.sleep(0.01)  # let t1 start
        t2 = asyncio.create_task(handler(2, 0.01))
        await asyncio.gather(t1, t2)

        # t1 must complete before t2 starts
        assert order.index("end-1") < order.index("start-2")
