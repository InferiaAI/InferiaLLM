"""Tests for CancelHandler — runs pulumi destroy."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.state_machine.jobs.model import (
    Phase, ProvisioningJob,
)
from orchestration.state_machine.phases.base import (
    PhaseContext,
)
from orchestration.state_machine.phases.cancel import (
    CancelHandler,
)


def _job() -> ProvisioningJob:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    return ProvisioningJob(
        id=uuid.uuid4(), node_id=uuid.uuid4(), pool_id=uuid.uuid4(),
        org_id="org-1", provider="aws",
        spec={"instance_class": "normal_gpu"},
        phase=Phase.CANCELLING, attempt_count=0,
        created_at=now, updated_at=now,
        pulumi_stack_outputs={"instance_id": "i-abc"},
    )


def _ctx():
    return PhaseContext(
        repo=MagicMock(), db=MagicMock(), emit_event=AsyncMock(),
        pulumi_env={"AWS_ACCESS_KEY_ID": "k", "AWS_SECRET_ACCESS_KEY": "s"},
    )


@pytest.mark.asyncio
async def test_happy_path_destroys_stack_and_returns_terminated():
    with patch(
        "orchestration.state_machine.phases."
        "cancel.run_pulumi_destroy_sync", return_value=None,
    ) as destroy:
        result = await CancelHandler().run(_job(), _ctx())
    assert result.next_phase == Phase.TERMINATED
    # Destroy MUST target the same local file backend + state_dir the
    # PulumiUpHandler created the stack in — otherwise it opens a different
    # backend, finds no stack, and "succeeds" while leaking the real EC2.
    kw = destroy.call_args.kwargs
    assert kw.get("state_dir")
    assert kw["env"].get("PULUMI_BACKEND_URL", "").startswith("file://")


@pytest.mark.asyncio
async def test_destroy_on_empty_state_is_noop():
    """If no AWS resources were ever created, destroy is a no-op."""
    j = _job()
    object.__setattr__(j, "pulumi_stack_outputs", {})  # bypass frozen
    with patch(
        "orchestration.state_machine.phases."
        "cancel.run_pulumi_destroy_sync", return_value=None,
    ) as destroy:
        result = await CancelHandler().run(j, _ctx())
    assert result.next_phase == Phase.TERMINATED
    destroy.assert_called_once()  # we still call it (idempotent)


@pytest.mark.asyncio
async def test_real_destroy_failure_stamps_metadata_and_reraises():
    """A REAL pulumi destroy failure (not the idempotent 'missing stack'
    case, which run_pulumi_destroy_sync swallows internally) must NOT advance
    to TERMINATED. The handler stamps metadata.destroy_failed on the
    inventory row and re-raises so the reconciler keeps the job retryable —
    the node must never silently show terminated while its EC2 lives.
    """
    from orchestration.repositories import inventory_repo as ir

    inv = MagicMock()
    inv.mark_destroy_failed = AsyncMock()
    boom = RuntimeError("pulumi destroy: api error in-use dependency")

    with patch(
        "orchestration.state_machine.phases."
        "cancel.run_pulumi_destroy_sync", side_effect=boom,
    ), patch.object(ir, "InventoryRepository", return_value=inv):
        with pytest.raises(RuntimeError, match="api error"):
            await CancelHandler().run(_job(), _ctx())

    # The failure was recorded with (node_id, reason) and NOT swallowed.
    inv.mark_destroy_failed.assert_awaited_once()
    args = inv.mark_destroy_failed.await_args.args
    assert "RuntimeError" in args[1] and "api error" in args[1]


@pytest.mark.asyncio
async def test_destroy_failure_metadata_record_error_does_not_mask_destroy_exc():
    """If recording the destroy_failed flag ALSO fails, the original destroy
    exception must still propagate (the reconciler needs the real error to
    classify retry-vs-fail)."""
    from orchestration.repositories import inventory_repo as ir

    inv = MagicMock()
    inv.mark_destroy_failed = AsyncMock(side_effect=RuntimeError("DB down"))

    with patch(
        "orchestration.state_machine.phases."
        "cancel.run_pulumi_destroy_sync",
        side_effect=ValueError("destroy exploded"),
    ), patch.object(ir, "InventoryRepository", return_value=inv):
        with pytest.raises(ValueError, match="destroy exploded"):
            await CancelHandler().run(_job(), _ctx())
