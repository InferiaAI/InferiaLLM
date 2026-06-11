"""Tests for the reconciler's graceful shutdown behavior."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestration.provisioning_state_machine.jobs.model import (
    Phase, PhaseResult, ProvisioningJob,
)
from orchestration.provisioning_state_machine.reconciler.loop import (
    ProvisioningReconciler,
)


@pytest.mark.asyncio
async def test_stop_with_drain_waits_up_to_grace_seconds_then_cancels():
    """In-flight handlers get grace seconds to complete; after that,
    they are cancelled (leases stay set, will expire naturally)."""
    handler_cancelled = False

    class _SlowHandler:
        name = Phase.PROVISIONING
        async def run(self, job, ctx):
            nonlocal handler_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                handler_cancelled = True
                raise
            return PhaseResult(next_phase=Phase.BOOTSTRAPPING)

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    j = ProvisioningJob(
        id=uuid.uuid4(), node_id=uuid.uuid4(), pool_id=uuid.uuid4(),
        org_id="org", provider="aws", spec={},
        phase=Phase.PROVISIONING, attempt_count=0,
        created_at=now, updated_at=now,
    )
    repo = MagicMock()
    repo.claim_next_job = AsyncMock(side_effect=[j, None, None])
    repo.renew_lease = AsyncMock(return_value=True)
    repo.release_lease = AsyncMock()
    repo.transition_to = AsyncMock()
    repo.schedule_retry = AsyncMock()
    repo.fail = AsyncMock()

    rec = ProvisioningReconciler(
        repo=repo, handlers={Phase.PROVISIONING: _SlowHandler()},
        emit_event=AsyncMock(), db=MagicMock(), concurrency=1,
        poll_interval_s=0.01, lease_seconds=10, renew_interval_s=1.0,
        lease_holder="t",
        load_aws_context=AsyncMock(return_value=(None, {})),
    )
    run_task = asyncio.create_task(rec.run())
    await asyncio.sleep(0.05)  # let the handler start

    await rec.stop_with_drain(grace_seconds=0.05)
    run_task.cancel()
    try:
        await run_task
    except asyncio.CancelledError:
        pass

    assert handler_cancelled is True
