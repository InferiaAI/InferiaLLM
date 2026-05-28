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

from inferia.services.orchestration.services.provisioning.errors import (
    AMINotFoundError, InvalidCredentialsError, InvalidInstanceTypeError,
    InvalidSpecError, PulumiCliMissingError, SecurityGroupNotFoundError,
    SubnetNotFoundError,
)
from inferia.services.orchestration.services.provisioning.jobs.model import (
    Phase, ProvisioningJob,
)
from inferia.services.orchestration.services.provisioning.phases.base import (
    PhaseContext,
)
from inferia.services.orchestration.services.provisioning.phases.preflight import (
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


def _ctx():
    return PhaseContext(
        repo=MagicMock(),
        db=MagicMock(),
        emit_event=AsyncMock(),
    )


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
            "inferia.services.orchestration.services.provisioning.phases."
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
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_credentials",
            return_value={"Account": "123"},
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_subnet_exists", return_value=None,
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_security_group_exists", return_value=None,
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.resolve_ami", return_value="ami-abc",
        ):
        result = await PreflightHandler().run(_job(), _ctx())
    assert result.next_phase == Phase.PROVISIONING
    # T14 code review: outputs must carry the keys T15 PulumiUpHandler
    # reads back.
    assert result.outputs == {
        "ami_id": "ami-abc",
        "region": "us-east-1",
        "instance_class": "normal_gpu",
        "instance_type": "g6.xlarge",
    }


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
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_credentials", return_value={},
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
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
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_credentials", return_value={},
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_subnet_exists", return_value=None,
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_security_group_exists",
            side_effect=SecurityGroupNotFoundError("sg-abc"),
        ):
        with pytest.raises(SecurityGroupNotFoundError):
            await PreflightHandler().run(job, _ctx())


@pytest.mark.asyncio
async def test_ami_check_failure_raises():
    with patch("shutil.which", return_value="/usr/local/bin/pulumi"), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_credentials", return_value={},
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_subnet_exists", return_value=None,
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.verify_security_group_exists", return_value=None,
        ), \
         patch(
            "inferia.services.orchestration.services.provisioning.phases."
            "preflight.resolve_ami",
            side_effect=AMINotFoundError("ami-x not in us-east-1"),
        ):
        with pytest.raises(AMINotFoundError):
            await PreflightHandler().run(_job(), _ctx())
