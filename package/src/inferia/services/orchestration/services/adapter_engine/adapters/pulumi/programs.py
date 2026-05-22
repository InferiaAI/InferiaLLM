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


def build_gce_program(
    *,
    pool_id: str,
    org_id: str,
    bootstrap_id: str,
    machine_type: str,
    zone: str,
    image_uri: str,
    user_data: str,
) -> Callable[[], None]:
    """Return a Pulumi program for a single gcp.compute.Instance."""

    def _program() -> None:
        import pulumi
        import pulumi_gcp as gcp

        instance = gcp.compute.Instance(
            f"inferia-pool-{pool_id}",
            name=f"inferia-pool-{pool_id}",
            machine_type=machine_type,
            zone=zone,
            boot_disk=gcp.compute.InstanceBootDiskArgs(
                initialize_params=gcp.compute.InstanceBootDiskInitializeParamsArgs(
                    image=image_uri,
                ),
            ),
            network_interfaces=[
                gcp.compute.InstanceNetworkInterfaceArgs(
                    network="default",
                    access_configs=[gcp.compute.InstanceNetworkInterfaceAccessConfigArgs()],
                ),
            ],
            metadata={
                "startup-script": user_data,
                "inferia-pool-id": pool_id,
                "inferia-org-id": org_id,
                "inferia-bootstrap-id": bootstrap_id,
            },
            labels={
                "inferia-pool-id": pool_id,
            },
        )
        pulumi.export("instance_id", instance.id)

    return _program
