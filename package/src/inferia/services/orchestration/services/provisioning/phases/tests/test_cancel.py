"""Tests for CancelHandler — runs pulumi destroy."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext,
)
from inferia.services.orchestration.services.provisioning.phases.cancel import (
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
        "inferia.services.orchestration.services.provisioning.phases."
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
        "inferia.services.orchestration.services.provisioning.phases."
        "cancel.run_pulumi_destroy_sync", return_value=None,
    ) as destroy:
        result = await CancelHandler().run(j, _ctx())
    assert result.next_phase == Phase.TERMINATED
    destroy.assert_called_once()  # we still call it (idempotent)
