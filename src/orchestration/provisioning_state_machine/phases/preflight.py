"""PreflightHandler — runs the 8 preflight checks before pulumi up.

Each check raises a typed ProvisioningError if it fails; the classifier
maps them to PERMANENT (fail-fast) so operators see actionable errors
immediately rather than after waiting for stack.up().

The helpers (verify_credentials, verify_subnet_exists, ...) are imported
at module scope so tests can patch them via module path. resolve_ami
is also imported from the AMI module.
"""
from __future__ import annotations

import shutil
from typing import Any

from providers.aws.instance_catalog import (
    lookup,
)
from providers.pulumi.ami import (
    resolve_ami,
)
from providers.pulumi.credentials import (
    verify_credentials,
)
from orchestration.provisioning_state_machine.errors import (
    AMINotFoundError, InvalidCredentialsError, InvalidInstanceTypeError,
    InvalidSpecError, PulumiCliMissingError, SecurityGroupNotFoundError,
    SubnetNotFoundError,
)
from orchestration.provisioning_state_machine.jobs.model import (
    Phase, PhaseResult, ProvisioningJob,
)
from orchestration.provisioning_state_machine.phases.base import (
    PhaseContext,
)


# Defined here for the same reason as credentials._boto3_sts_client:
# extracted so tests can patch without bringing boto3 into the import path.


def verify_subnet_exists(*, region: str, subnet_id: str, creds: Any) -> None:
    """Raise SubnetNotFoundError if the subnet does not exist."""
    from botocore.exceptions import ClientError
    import boto3
    ec2 = boto3.client(
        "ec2", region_name=region,
        aws_access_key_id=creds.access_key_id,
        aws_secret_access_key=creds.secret_access_key,
    )
    try:
        ec2.describe_subnets(SubnetIds=[subnet_id])
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code", "")
        if code == "InvalidSubnetID.NotFound":
            raise SubnetNotFoundError(
                f"subnet {subnet_id!r} not found in {region}"
            ) from e
        raise


def verify_security_group_exists(*, region: str, sg_id: str, creds: Any) -> None:
    """Raise SecurityGroupNotFoundError if the security group does not exist."""
    from botocore.exceptions import ClientError
    import boto3
    ec2 = boto3.client(
        "ec2", region_name=region,
        aws_access_key_id=creds.access_key_id,
        aws_secret_access_key=creds.secret_access_key,
    )
    try:
        ec2.describe_security_groups(GroupIds=[sg_id])
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code", "")
        if code == "InvalidGroup.NotFound":
            raise SecurityGroupNotFoundError(
                f"security group {sg_id!r} not found in {region}"
            ) from e
        raise


class PreflightHandler:
    """Phase: PREFLIGHT.

    Validates everything that's cheap to validate before kicking off
    pulumi up. Any failure here is a fast PERMANENT error with an
    operator-actionable hint."""

    name = Phase.PREFLIGHT

    async def run(self, job: ProvisioningJob, ctx: PhaseContext) -> PhaseResult:
        spec = job.spec or {}

        # Mark the phase as running so the dashboard timeline shows it
        # animating; without a non-"log" row this phase is invisible in
        # summarize_phases (which filters status="log").
        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.PREFLIGHT,
            status="running", message="Validating spec, credentials and AMI",
        )

        # 1. Pulumi CLI present.
        if shutil.which("pulumi") is None:
            raise PulumiCliMissingError(
                "pulumi binary not found on PATH inside inferia-app"
            )

        # 2. Required spec fields.
        for field in ("instance_class", "instance_type", "region"):
            if not spec.get(field):
                raise InvalidSpecError(
                    f"spec is missing required field: {field}"
                )
        instance_class = spec["instance_class"]
        instance_type = spec["instance_type"]
        region = spec["region"]

        # 3. instance_type ∈ catalog.
        it = lookup(instance_type)
        if it is None:
            raise InvalidInstanceTypeError(
                f"unknown instance type: {instance_type!r}"
            )

        # 4. class/type pairing.
        if it.cls != instance_class:
            raise InvalidInstanceTypeError(
                f"instance type {instance_type!r} belongs to class {it.cls!r}, "
                f"not {instance_class!r}"
            )

        # 5. Creds work.
        creds = ctx.aws_creds  # injected by the reconciler before dispatch
        if creds is None:
            # The reconciler's load_aws_context returned None — AWS creds are
            # not configured (Settings -> Providers -> AWS) or load_aws_context
            # was never wired. Fail cleanly instead of crashing on
            # None.access_key_id deep inside boto3.
            raise InvalidCredentialsError(
                "AWS credentials are not configured for this organisation; "
                "set them under Settings -> Providers -> AWS before deploying "
                "to an AWS pool"
            )
        identity = verify_credentials(creds)
        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.PREFLIGHT,
            status="log",
            message=f"AWS credentials verified (Account {identity.get('Account','?')})",
        )

        # 6. Subnet (optional — only if provider config supplies one).
        if subnet := spec.get("subnet_id"):
            verify_subnet_exists(region=region, subnet_id=subnet, creds=creds)

        # 7. Security group (optional).
        if sg := spec.get("security_group_id"):
            verify_security_group_exists(region=region, sg_id=sg, creds=creds)

        # 8. AMI resolves (explicit spec.ami_id wins; fallback to SSM lookup).
        explicit_ami = spec.get("ami_id")
        if explicit_ami:
            ami = explicit_ami
        else:
            try:
                ami = resolve_ami(region=region, instance_class=instance_class, creds=creds)
            except Exception as e:
                raise AMINotFoundError(
                    f"no AMI available for {instance_class} in {region}: {e}"
                ) from e
        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.PREFLIGHT,
            status="log", message=f"AMI resolved: {ami}",
        )

        await ctx.emit_event(
            pool_id=job.pool_id, node_id=job.node_id, phase=Phase.PREFLIGHT,
            status="succeeded", message="Preflight checks passed",
        )

        return PhaseResult(
            next_phase=Phase.PROVISIONING,
            outputs={"ami_id": ami, "region": region,
                       "instance_class": instance_class,
                       "instance_type": instance_type},
        )
