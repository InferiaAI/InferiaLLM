"""Tests for PulumiUpHandler."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
    StackOutputs,
)
from inferia.services.orchestration.services.provisioning.errors import (
    AWSThrottledError, InvalidCredentialsError,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext,
)
from inferia.services.orchestration.services.provisioning.phases.pulumi_up import (
    PulumiUpHandler,
)


def _job(spec: dict | None = None) -> ProvisioningJob:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    return ProvisioningJob(
        id=uuid.uuid4(), node_id=uuid.uuid4(), pool_id=uuid.uuid4(),
        org_id="org-1", provider="aws",
        spec=spec or {"instance_class": "normal_gpu",
                       "instance_type": "g6.xlarge",
                       "region": "us-east-1", "ami_id": "ami-abc"},
        phase=Phase.PROVISIONING, attempt_count=0,
        created_at=now, updated_at=now,
        pulumi_stack_outputs={"ami_id": "ami-abc"},
    )


def _ctx():
    return PhaseContext(
        repo=MagicMock(), db=MagicMock(),
        emit_event=AsyncMock(),
        aws_creds=MagicMock(),
        pulumi_env={"AWS_ACCESS_KEY_ID": "k", "AWS_SECRET_ACCESS_KEY": "s"},
    )


@pytest.mark.asyncio
async def test_happy_path_returns_bootstrapping_with_outputs():
    outputs = StackOutputs(
        instance_id="i-abc", public_dns="ec2-1.compute.amazonaws.com",
        region="us-east-1", ami_id="ami-abc",
    )
    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "pulumi_up.run_pulumi_up_sync", return_value=outputs,
    ):
        result = await PulumiUpHandler().run(_job(), _ctx())
    assert result.next_phase == Phase.BOOTSTRAPPING
    assert result.outputs == {
        "instance_id": "i-abc",
        "public_dns": "ec2-1.compute.amazonaws.com",
        "region": "us-east-1",
        "ami_id": "ami-abc",
    }


@pytest.mark.asyncio
async def test_throttled_error_propagates():
    """TransientError from run_pulumi_up_sync propagates — reconciler retries."""
    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "pulumi_up.run_pulumi_up_sync",
        side_effect=AWSThrottledError("rate limited"),
    ):
        with pytest.raises(AWSThrottledError):
            await PulumiUpHandler().run(_job(), _ctx())


@pytest.mark.asyncio
async def test_auth_failure_propagates_as_permanent():
    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "pulumi_up.run_pulumi_up_sync",
        side_effect=InvalidCredentialsError("bad creds"),
    ):
        with pytest.raises(InvalidCredentialsError):
            await PulumiUpHandler().run(_job(), _ctx())


@pytest.mark.asyncio
async def test_stack_name_uses_deterministic_org_pool_node_format():
    captured = {}
    def _spy(*, stack_name, program, env):
        captured["stack_name"] = stack_name
        return StackOutputs(instance_id="i", public_dns=None, region=None, ami_id=None)
    j = _job()
    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "pulumi_up.run_pulumi_up_sync", side_effect=_spy,
    ):
        await PulumiUpHandler().run(j, _ctx())
    assert captured["stack_name"] == f"{j.org_id}-{j.pool_id}-{j.node_id}"


@pytest.mark.asyncio
async def test_emit_event_logs_progress():
    outputs = StackOutputs(
        instance_id="i-abc", public_dns=None, region=None, ami_id="ami-x",
    )
    ctx = _ctx()
    with patch(
        "inferia.services.orchestration.services.provisioning.phases."
        "pulumi_up.run_pulumi_up_sync", return_value=outputs,
    ):
        await PulumiUpHandler().run(_job(), ctx)
    # At least one log event emitted (start) + one success event.
    assert ctx.emit_event.await_count >= 2
