"""Tests for PreflightHandler — runs the 8 preflight checks listed in the spec:

1. pulumi CLI present on PATH
2. AWS creds verify via sts:GetCallerIdentity
3. spec contains required fields (instance_class, instance_type, region)
4. instance_type ∈ catalog
5. instance_type.cls matches spec.instance_class
6. subnet (from ProvidersConfig) exists in region
7. security group (from ProvidersConfig) exists in region
8. AMI resolvable for region + instance_class
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.state_machine.errors import (
    AMINotFoundError, InvalidCredentialsError, InvalidInstanceTypeError,
    InvalidSpecError, PulumiCliMissingError, SecurityGroupNotFoundError,
    SubnetNotFoundError,
)
from orchestration.state_machine.jobs.model import (
    Phase, ProvisioningJob,
)
from orchestration.state_machine.phases.base import (
    PhaseContext,
)
from orchestration.state_machine.phases.preflight import (
    PreflightHandler,
)


def _job(spec: dict | None = None) -> ProvisioningJob:
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    return ProvisioningJob(
        id=uuid.uuid4(), node_id=uuid.uuid4(), pool_id=uuid.uuid4(),
        org_id="org-1", provider="aws",
        spec=spec or {"instance_class": "normal_gpu", "instance_type": "g6.xlarge",
                       "region": "us-east-1"},
        phase=Phase.PREFLIGHT, attempt_count=0,
        created_at=now, updated_at=now,
    )


def _ctx(aws_creds: object = object()):
    # Default to a non-None sentinel — preflight now rejects aws_creds=None
    # before reaching verify_credentials (which the tests mock anyway).
    return PhaseContext(
        repo=MagicMock(),
        db=MagicMock(),
        emit_event=AsyncMock(),
        aws_creds=aws_creds,
    )


@pytest.mark.asyncio
async def test_none_aws_creds_raises_invalid_credentials():
    """When the reconciler couldn't load AWS creds (load_aws_context
    returned None), preflight must fail cleanly with InvalidCredentialsError
    instead of crashing on None.access_key_id deep in boto3."""
    from orchestration.state_machine.errors import (
        InvalidCredentialsError,
    )
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"):
        with pytest.raises(InvalidCredentialsError):
            await PreflightHandler().run(_job(), _ctx(aws_creds=None))


@pytest.mark.asyncio
async def test_pulumi_cli_missing_raises():
    with patch("shutil.which", return_value=None):
        with pytest.raises(PulumiCliMissingError):
            await PreflightHandler().run(_job(), _ctx())


@pytest.mark.asyncio
async def test_spec_missing_instance_type_raises_invalid_spec():
    bad = _job({"instance_class": "normal_gpu", "region": "us-east-1"})
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"):
        with pytest.raises(InvalidSpecError) as exc_info:
            await PreflightHandler().run(bad, _ctx())
        assert "instance_type" in str(exc_info.value)


@pytest.mark.asyncio
async def test_unknown_instance_type_raises_invalid_instance_type():
    bad = _job({"instance_class": "normal_gpu", "instance_type": "z9.imaginary",
                  "region": "us-east-1"})
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"):
        with pytest.raises(InvalidInstanceTypeError):
            await PreflightHandler().run(bad, _ctx())


@pytest.mark.asyncio
async def test_instance_type_class_mismatch_raises():
    """instance_type='c6i.xlarge' (cpu) with instance_class='normal_gpu' is invalid."""
    bad = _job({"instance_class": "normal_gpu", "instance_type": "c6i.xlarge",
                  "region": "us-east-1"})
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"):
        with pytest.raises(InvalidInstanceTypeError):
            await PreflightHandler().run(bad, _ctx())


@pytest.mark.asyncio
async def test_credential_verification_failure_raises():
    """If sts:GetCallerIdentity fails, raise InvalidCredentialsError."""
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_credentials",
            side_effect=InvalidCredentialsError("bad creds"),
        ):
        with pytest.raises(InvalidCredentialsError):
            await PreflightHandler().run(_job(), _ctx())


@pytest.mark.asyncio
async def test_happy_path_returns_provisioning():
    """All preflight checks pass → next_phase=PROVISIONING."""
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_credentials",
            return_value={"Account": "123"},
        ), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_subnet_exists", return_value=None,
        ), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_security_group_exists", return_value=None,
        ), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.resolve_ami", return_value="ami-abc",
        ), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.ami_root_volume_gb", return_value=None,
        ):
        ctx = _ctx()
        result = await PreflightHandler().run(_job(), ctx)
    assert result.next_phase == Phase.PROVISIONING
    # T14 code review: outputs must carry the keys T15 PulumiUpHandler
    # reads back. (No root_volume_gb here: spec carries none and the AMI
    # lookup returned None, so nothing to floor.)
    assert result.outputs == {
        "ami_id": "ami-abc",
        "region": "us-east-1",
        "instance_class": "normal_gpu",
        "instance_type": "g6.xlarge",
    }
    # The dashboard timeline depends on a non-"log" running + succeeded row
    # for PREFLIGHT (summarize_phases filters status="log"). Assert both.
    statuses = [
        kw["status"] for (_a, kw) in ctx.emit_event.await_args_list
        if kw.get("phase") == Phase.PREFLIGHT
    ]
    assert "running" in statuses
    assert "succeeded" in statuses


@pytest.mark.parametrize("missing_field", ["instance_class", "instance_type", "region"])
@pytest.mark.asyncio
async def test_spec_missing_required_field_raises_invalid_spec(missing_field):
    """Each of the 3 required fields, when missing, raises InvalidSpecError
    with the field name in the message."""
    full_spec = {"instance_class": "normal_gpu", "instance_type": "g6.xlarge",
                   "region": "us-east-1"}
    full_spec.pop(missing_field)
    bad = _job(full_spec)
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"):
        with pytest.raises(InvalidSpecError) as exc_info:
            await PreflightHandler().run(bad, _ctx())
        assert missing_field in str(exc_info.value)


@pytest.mark.asyncio
async def test_subnet_check_failure_raises():
    # subnet_id must be present in spec for the optional subnet check
    # to fire (see preflight.py step 6).
    job = _job({
        "instance_class": "normal_gpu", "instance_type": "g6.xlarge",
        "region": "us-east-1", "subnet_id": "subnet-abc",
    })
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_credentials", return_value={},
        ), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_subnet_exists",
            side_effect=SubnetNotFoundError("subnet-abc"),
        ):
        with pytest.raises(SubnetNotFoundError):
            await PreflightHandler().run(job, _ctx())


@pytest.mark.asyncio
async def test_security_group_check_failure_raises():
    # security_group_id must be present in spec for the optional SG
    # check to fire (see preflight.py step 7).
    job = _job({
        "instance_class": "normal_gpu", "instance_type": "g6.xlarge",
        "region": "us-east-1", "security_group_id": "sg-abc",
    })
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_credentials", return_value={},
        ), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_subnet_exists", return_value=None,
        ), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_security_group_exists",
            side_effect=SecurityGroupNotFoundError("sg-abc"),
        ):
        with pytest.raises(SecurityGroupNotFoundError):
            await PreflightHandler().run(job, _ctx())


@pytest.mark.asyncio
async def test_ami_check_failure_raises():
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_credentials", return_value={},
        ), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_subnet_exists", return_value=None,
        ), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_security_group_exists", return_value=None,
        ), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.resolve_ami",
            side_effect=AMINotFoundError("ami-x not in us-east-1"),
        ):
        with pytest.raises(AMINotFoundError):
            await PreflightHandler().run(_job(), _ctx())


# ---------------------------------------------------------------------------
# T5 provider-config-ux: explicit spec.ami_id skips resolve_ami
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preflight_prefers_spec_ami_id(monkeypatch):
    """When spec carries an explicit ami_id, resolve_ami must NOT be called.

    This ensures that a deploy-time ami_id (required for vLLM) is used
    verbatim by the preflight phase instead of triggering an SSM lookup
    that may look up a different AMI or fail in non-standard regions.
    """
    import orchestration.state_machine.phases.preflight as pf

    def _must_not_call(**_k):
        raise AssertionError("resolve_ami must NOT be called when spec has ami_id")

    monkeypatch.setattr(pf, "resolve_ami", _must_not_call)
    monkeypatch.setattr(pf, "ami_root_volume_gb", lambda *a, **k: None)

    job = _job(spec={
        "instance_class": "normal_gpu",
        "instance_type": "g6.xlarge",
        "region": "us-east-1",
        "ami_id": "ami-explicit0123",
    })

    with patch("shutil.which", return_value="/usr/local/bin/pulumi"), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_credentials",
            return_value={"Account": "123"},
        ):
        ctx = _ctx()
        result = await PreflightHandler().run(job, ctx)

    assert result.outputs["ami_id"] == "ami-explicit0123"
    assert result.next_phase == Phase.PROVISIONING


@pytest.mark.asyncio
async def test_preflight_falls_back_to_resolve_ami_when_spec_has_no_ami_id():
    """When spec.ami_id is absent, resolve_ami is still called (existing behaviour)."""
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_credentials",
            return_value={"Account": "123"},
        ), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.resolve_ami",
            return_value="ami-auto-pick",
        ) as mock_resolve, \
         patch(
            "orchestration.state_machine.phases."
            "preflight.ami_root_volume_gb", return_value=None,
        ):
        result = await PreflightHandler().run(_job(), _ctx())

    mock_resolve.assert_called_once()
    assert result.outputs["ami_id"] == "ami-auto-pick"


@pytest.mark.asyncio
async def test_preflight_floors_root_volume_at_ami_snapshot():
    """A baked engine AMI whose snapshot (130GB) exceeds the requested root
    volume (100GB) must raise the launch root volume to 130 so RunInstances
    doesn't fail InvalidBlockDeviceMapping. The corrected value is pinned into
    outputs (build_program prefers it over spec)."""
    job = _job(spec={
        "instance_class": "normal_gpu", "instance_type": "g6.xlarge",
        "region": "us-east-1", "ami_id": "ami-baked", "root_volume_gb": 100,
    })
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_credentials", return_value={"Account": "123"},
        ), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.ami_root_volume_gb", return_value=130,
        ):
        ctx = _ctx()
        result = await PreflightHandler().run(job, ctx)

    assert result.outputs["root_volume_gb"] == 130
    # An operator-visible log row explains the bump.
    msgs = [kw.get("message", "") for (_a, kw) in ctx.emit_event.await_args_list]
    assert any("Root volume raised to 130GB" in m for m in msgs)


@pytest.mark.asyncio
async def test_preflight_keeps_requested_root_volume_when_larger():
    """When the requested root volume already exceeds the AMI snapshot, keep
    it (don't shrink) and don't emit a bump log."""
    job = _job(spec={
        "instance_class": "normal_gpu", "instance_type": "g6.xlarge",
        "region": "us-east-1", "ami_id": "ami-baked", "root_volume_gb": 200,
    })
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.verify_credentials", return_value={"Account": "123"},
        ), \
         patch(
            "orchestration.state_machine.phases."
            "preflight.ami_root_volume_gb", return_value=130,
        ):
        ctx = _ctx()
        result = await PreflightHandler().run(job, ctx)

    assert result.outputs["root_volume_gb"] == 200
    msgs = [kw.get("message", "") for (_a, kw) in ctx.emit_event.await_args_list]
    assert not any("Root volume raised" in m for m in msgs)
