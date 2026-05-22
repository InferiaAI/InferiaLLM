"""Inline Pulumi programs used by the cloud adapters.

Each `build_*_program(...)` returns a zero-arg callable suitable for
passing to `pulumi.automation.create_or_select_stack(program=...)`.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def build_ec2_program(
    *,
    pool_id: str,
    org_id: str,
    bootstrap_id: str,
    instance_type: str,
    region: str,
    ami_id: str,
    subnet_id: Optional[str],
    security_group_ids: Optional[List[str]],
    iam_instance_profile: Optional[str],
    root_volume_gb: int,
    user_data: str,
    use_spot: bool = False,
) -> Callable[[], None]:
    """Return a Pulumi program that defines exactly one
    aws.ec2.Instance for the given pool."""

    def _program() -> None:
        import pulumi
        import pulumi_aws as aws

        root_bd = aws.ec2.InstanceRootBlockDeviceArgs(
            volume_size=root_volume_gb,
            volume_type="gp3",
        )

        kwargs: Dict[str, Any] = dict(
            instance_type=instance_type,
            ami=ami_id,
            user_data=user_data,
            root_block_device=root_bd,
            tags={
                "Name": f"inferia-pool-{pool_id}",
                "InferiaPoolId": pool_id,
                "InferiaOrgId": org_id,
                "InferiaBootstrapId": bootstrap_id,
            },
        )
        if subnet_id:
            kwargs["subnet_id"] = subnet_id
        if security_group_ids:
            kwargs["vpc_security_group_ids"] = security_group_ids
        if iam_instance_profile:
            kwargs["iam_instance_profile"] = iam_instance_profile
        if use_spot:
            kwargs["instance_market_options"] = aws.ec2.InstanceInstanceMarketOptionsArgs(
                market_type="spot",
            )

        instance = aws.ec2.Instance(f"inferia-pool-{pool_id}", **kwargs)
        pulumi.export("instance_id", instance.id)
        pulumi.export("public_dns", instance.public_dns)
        pulumi.export("private_ip", instance.private_ip)

    return _program
