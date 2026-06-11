"""Tests for BootstrapHandler — polls compute_inventory.state waiting for
the worker on the EC2 instance to register and transition to 'ready'."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from inferia.services.orchestration.services.provisioning.errors import (
    NetworkError, TransientError,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext,
)
from inferia.services.orchestration.services.provisioning.phases.bootstrap import (
    BootstrapHandler,
)


def _job() -> ProvisioningJob:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    return ProvisioningJob(
        id=uuid.uuid4(), node_id=uuid.uuid4(), pool_id=uuid.uuid4(),
        org_id="org-1", provider="aws", spec={},
        phase=Phase.BOOTSTRAPPING, attempt_count=0,
        created_at=now, updated_at=now,
    )


def _ctx(*, bootstrap_timeout_s=600.0, get_inventory_states):
    """get_inventory_states yields state strings on successive polls."""
    states = iter(get_inventory_states)
    async def _poll(*, node_id):
        try:
            return {"state": next(states)}
        except StopIteration:
            return {"state": "provisioning"}
    inv = MagicMock()
    # NOTE: pass _poll directly (not wrapped in a lambda). AsyncMock awaits
    # coroutine functions assigned to side_effect, but a sync lambda that
    # *returns* a coroutine causes AsyncMock to return the un-awaited
    # coroutine to the caller (Python 3.12 unittest.mock semantics).
    inv.get_node = AsyncMock(side_effect=_poll)
    return PhaseContext(
        repo=MagicMock(),
        db=MagicMock(),
        emit_event=AsyncMock(),
        bootstrap_timeout_s=bootstrap_timeout_s,
    ), inv


@pytest.mark.asyncio
async def test_returns_ready_immediately_when_already_ready():
    ctx, inv = _ctx(get_inventory_states=["ready"])
    handler = BootstrapHandler(inventory_repo=inv, poll_interval_s=0.01)
    result = await handler.run(_job(), ctx)
    assert result.next_phase == Phase.READY


@pytest.mark.asyncio
async def test_polls_until_state_becomes_ready():
    ctx, inv = _ctx(get_inventory_states=[
        "provisioning", "provisioning", "ready",
    ])
    handler = BootstrapHandler(inventory_repo=inv, poll_interval_s=0.01)
    result = await handler.run(_job(), ctx)
    assert result.next_phase == Phase.READY
    assert inv.get_node.await_count >= 3


@pytest.mark.asyncio
async def test_raises_transient_error_on_timeout():
    """Bootstrap deadline elapses without the worker registering."""
    ctx, inv = _ctx(
        bootstrap_timeout_s=0.05,
        get_inventory_states=["provisioning"] * 20,
    )
    handler = BootstrapHandler(inventory_repo=inv, poll_interval_s=0.01)
    with pytest.raises(TransientError):
        await handler.run(_job(), ctx)


@pytest.mark.asyncio
async def test_raises_permanent_when_node_state_becomes_failed():
    """If the worker's startup script crashes, inventory.state may flip to
    'failed' directly. Bootstrap should fail-loud, not poll forever."""
    from inferia.services.orchestration.services.provisioning.errors import (
        PermanentError,
    )
    ctx, inv = _ctx(get_inventory_states=["provisioning", "failed"])
    handler = BootstrapHandler(inventory_repo=inv, poll_interval_s=0.01)
    with pytest.raises(PermanentError):
        await handler.run(_job(), ctx)


@pytest.mark.asyncio
async def test_emits_running_log_at_least_once():
    ctx, inv = _ctx(get_inventory_states=["ready"])
    handler = BootstrapHandler(inventory_repo=inv, poll_interval_s=0.01)
    await handler.run(_job(), ctx)
    assert ctx.emit_event.await_count >= 1


@pytest.mark.asyncio
async def test_emits_terminal_ready_succeeded_event():
    """The READY phase has no handler of its own, so bootstrap must emit a
    succeeded row for Phase.READY so the dashboard timeline shows the final
    completed tick."""
    ctx, inv = _ctx(get_inventory_states=["ready"])
    handler = BootstrapHandler(inventory_repo=inv, poll_interval_s=0.01)
    await handler.run(_job(), ctx)
    ready_succeeded = [
        kw for (_a, kw) in ctx.emit_event.await_args_list
        if kw.get("phase") == Phase.READY and kw.get("status") == "succeeded"
    ]
    assert len(ready_succeeded) == 1
