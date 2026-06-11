"""Tests for PulumiUpHandler."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from providers.pulumi.pulumi_aws_adapter import (
    StackOutputs,
)
from services.orchestration.provisioning_state_machine.errors import (
    AWSThrottledError, InvalidCredentialsError,
)
from services.orchestration.provisioning_state_machine.jobs.model import (
    Phase, ProvisioningJob,
)
from services.orchestration.provisioning_state_machine.phases.base import (
    PhaseContext,
)
from services.orchestration.provisioning_state_machine.phases.pulumi_up import (
    PulumiUpHandler,
)


def _job(spec: dict | None = None) -> ProvisioningJob:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    # Default spec carries user_data so the handler SKIPS _inject_aws_bootstrap
    # (which mints a token + does DB work) — the injection path has its own
    # dedicated test below.
    return ProvisioningJob(
        id=uuid.uuid4(), node_id=uuid.uuid4(), pool_id=uuid.uuid4(),
        org_id="org-1", provider="aws",
        spec=spec or {"instance_class": "normal_gpu",
                       "instance_type": "g6.xlarge",
                       "region": "us-east-1", "ami_id": "ami-abc",
                       "user_data": "#!/bin/sh\necho hi"},
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
        "services.orchestration.provisioning_state_machine.phases."
        "pulumi_up.run_pulumi_up_sync", return_value=outputs,
    ):
        result = await PulumiUpHandler().run(_job(), _ctx())
    assert result.next_phase == Phase.BOOTSTRAPPING
    assert result.outputs == {
        "instance_id": "i-abc",
        "public_dns": "ec2-1.compute.amazonaws.com",
        "private_ip": None,
        "region": "us-east-1",
        "ami_id": "ami-abc",
    }


@pytest.mark.asyncio
async def test_outputs_omit_region_ami_when_stack_does_not_export_them():
    """When the stack exports no region/ami_id (None), the PhaseResult must
    NOT include them — otherwise the jsonb '||' merge clobbers the values
    PreflightHandler already wrote into pulumi_stack_outputs with None."""
    outputs = StackOutputs(
        instance_id="i-abc", public_dns=None, region=None, ami_id=None,
    )
    with patch(
        "services.orchestration.provisioning_state_machine.phases."
        "pulumi_up.run_pulumi_up_sync", return_value=outputs,
    ):
        result = await PulumiUpHandler().run(_job(), _ctx())
    assert "region" not in result.outputs
    assert "ami_id" not in result.outputs
    assert result.outputs["instance_id"] == "i-abc"


@pytest.mark.asyncio
async def test_aws_bootstrap_injected_when_user_data_absent():
    """A spec with no user_data triggers _inject_aws_bootstrap, which mints a
    token + builds cloud-init and injects user_data + bootstrap_id before
    build_program. We assert build_program saw a non-empty user_data."""
    job = _job(spec={"provider": "aws", "instance_class": "normal_gpu",
                      "instance_type": "g6.xlarge", "region": "us-east-1",
                      "pool_id": "p", "org_id": "o", "gpu_count": 1})
    captured = {}

    def _capture_program(*, spec, stack_outputs):
        captured["user_data"] = spec.get("user_data")
        captured["bootstrap_id"] = spec.get("bootstrap_id")
        captured["node_id"] = spec.get("node_id")
        return lambda: None

    # A db whose .acquire() yields an async-context conn with fetchrow/execute.
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"node_name": "node-xyz"})
    conn.execute = AsyncMock(return_value=None)
    db = MagicMock()
    db.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    ctx = PhaseContext(
        repo=MagicMock(), db=db, emit_event=AsyncMock(),
        aws_creds=MagicMock(),
        pulumi_env={"AWS_ACCESS_KEY_ID": "k", "AWS_SECRET_ACCESS_KEY": "s"},
    )

    outputs = StackOutputs(instance_id="i", public_dns=None,
                           region=None, ami_id=None)
    with patch(
        "services.orchestration.provisioning_state_machine.phases."
        "pulumi_up.build_program", side_effect=_capture_program,
    ), patch(
        "services.orchestration.provisioning_state_machine.phases."
        "pulumi_up.run_pulumi_up_sync", return_value=outputs,
    ), patch(
        "services.orchestration.worker_controller.auth."
        "mint_bootstrap_token", new=AsyncMock(return_value=("tok", uuid.uuid4())),
    ), patch(
        "services.orchestration.repositories.pool_repo."
        "ComputePoolRepository.get_or_generate_inference_token",
        new=AsyncMock(return_value="inf-tok"),
    ):
        await PulumiUpHandler().run(job, ctx)

    assert captured["user_data"]  # non-empty cloud-init was injected
    assert captured["bootstrap_id"]
    # The setdefault threading puts the placeholder node id into the spec so
    # build_program stamps it as the per-node sweep tag (InferiaNodeId).
    assert captured["node_id"] == str(job.node_id)


@pytest.mark.asyncio
async def test_node_id_threaded_into_spec_for_tag_emission():
    """The handler setdefaults the placeholder's node_id into the spec so the
    launch program stamps an InferiaNodeId tag (consumed by the boto3 orphan
    sweep). Covers the threading independently of the bootstrap-injection path
    (this job already carries user_data, so _inject_aws_bootstrap is skipped)."""
    job = _job()
    captured = {}

    def _capture_program(*, spec, stack_outputs):
        captured["node_id"] = spec.get("node_id")
        return lambda: None

    outputs = StackOutputs(instance_id="i", public_dns=None,
                           region=None, ami_id=None)
    with patch(
        "services.orchestration.provisioning_state_machine.phases."
        "pulumi_up.build_program", side_effect=_capture_program,
    ), patch(
        "services.orchestration.provisioning_state_machine.phases."
        "pulumi_up.run_pulumi_up_sync", return_value=outputs,
    ):
        await PulumiUpHandler().run(job, _ctx())

    assert captured["node_id"] == str(job.node_id)


@pytest.mark.asyncio
async def test_throttled_error_propagates():
    """TransientError from run_pulumi_up_sync propagates — reconciler retries."""
    with patch(
        "services.orchestration.provisioning_state_machine.phases."
        "pulumi_up.run_pulumi_up_sync",
        side_effect=AWSThrottledError("rate limited"),
    ):
        with pytest.raises(AWSThrottledError):
            await PulumiUpHandler().run(_job(), _ctx())


@pytest.mark.asyncio
async def test_auth_failure_propagates_as_permanent():
    with patch(
        "services.orchestration.provisioning_state_machine.phases."
        "pulumi_up.run_pulumi_up_sync",
        side_effect=InvalidCredentialsError("bad creds"),
    ):
        with pytest.raises(InvalidCredentialsError):
            await PulumiUpHandler().run(_job(), _ctx())


@pytest.mark.asyncio
async def test_stack_name_is_deterministic_and_within_pulumi_limit():
    captured = {}
    def _spy(*, stack_name, program, env, state_dir=None, **_):
        captured["stack_name"] = stack_name
        captured["env"] = env
        captured["state_dir"] = state_dir
        return StackOutputs(instance_id="i", public_dns=None, region=None, ami_id=None)
    j = _job()
    with patch(
        "services.orchestration.provisioning_state_machine.phases."
        "pulumi_up.run_pulumi_up_sync", side_effect=_spy,
    ):
        await PulumiUpHandler().run(j, _ctx())
    # node_id uniquely identifies the provision; the old org-pool-node
    # scheme was three UUIDs (~110 chars) and pulumi rejects names >100.
    assert captured["stack_name"] == f"inferia-{j.node_id}"
    assert len(captured["stack_name"]) <= 100


@pytest.mark.asyncio
async def test_emit_event_logs_progress():
    outputs = StackOutputs(
        instance_id="i-abc", public_dns=None, region=None, ami_id="ami-x",
    )
    ctx = _ctx()
    with patch(
        "services.orchestration.provisioning_state_machine.phases."
        "pulumi_up.run_pulumi_up_sync", return_value=outputs,
    ):
        await PulumiUpHandler().run(_job(), ctx)
    # At least one log event emitted (start) + one success event.
    assert ctx.emit_event.await_count >= 2
