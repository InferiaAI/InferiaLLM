"""Tests for the lease.renew_loop helper used by the reconciler."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.orchestration.provisioning_state_machine.reconciler.lease import (
    renew_loop,
)


@pytest.mark.asyncio
async def test_renew_loop_calls_repo_renew_every_interval():
    repo = MagicMock()
    repo.renew_lease = AsyncMock(return_value=True)
    stop = asyncio.Event()

    async def trigger():
        await asyncio.sleep(0.1)
        stop.set()

    job_id = uuid.uuid4()
    await asyncio.gather(
        renew_loop(repo=repo, job_id=job_id, lease_holder="me",
                   renew_interval_s=0.03, lease_seconds=300, stop=stop),
        trigger(),
    )
    # Should have renewed at least twice in 0.1s with 0.03s interval.
    assert repo.renew_lease.await_count >= 2


@pytest.mark.asyncio
async def test_renew_loop_returns_false_signal_when_stolen():
    """If renew_lease returns False, the loop sets stop and returns False."""
    repo = MagicMock()
    repo.renew_lease = AsyncMock(return_value=False)
    stop = asyncio.Event()
    result = await renew_loop(
        repo=repo, job_id=uuid.uuid4(), lease_holder="me",
        renew_interval_s=0.01, lease_seconds=300, stop=stop,
    )
    assert result is False
    assert stop.is_set()


@pytest.mark.asyncio
async def test_renew_loop_stops_when_event_set():
    repo = MagicMock()
    repo.renew_lease = AsyncMock(return_value=True)
    stop = asyncio.Event()
    stop.set()  # already set
    result = await renew_loop(
        repo=repo, job_id=uuid.uuid4(), lease_holder="me",
        renew_interval_s=0.01, lease_seconds=300, stop=stop,
    )
    assert result is True
    # 0 or 1 renewals only (since stop is already set).
    assert repo.renew_lease.await_count <= 1
