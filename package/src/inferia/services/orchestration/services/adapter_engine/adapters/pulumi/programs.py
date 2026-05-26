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
        else:
            # No operator-provided SG → create a dedicated one that lets the
            # control plane dial back into the worker. Without this the
            # EC2's default SG only accepts traffic from the same SG, so
            # /v1/admin/workers/<id>/logs and /shell time out with
            # "upstream connect failed: timed out during opening handshake".
            #
            # 8080 is the worker's control port (see WORKER_ADVERTISE_URL in
            # bootstrap_builder). 22 stays closed by default — operators who
            # want SSH access add their own SG via providers config.
            sg = aws.ec2.SecurityGroup(
                f"inferia-worker-sg-{pool_id}",
                description="Inferia worker - control-plane reach-back",
                ingress=[
                    aws.ec2.SecurityGroupIngressArgs(
                        protocol="tcp",
                        from_port=8080,
                        to_port=8080,
                        cidr_blocks=["0.0.0.0/0"],
                        description="control plane WS reach-back",
                    ),
                    aws.ec2.SecurityGroupIngressArgs(
                        protocol="tcp",
                        from_port=22,
                        to_port=22,
                        cidr_blocks=["0.0.0.0/0"],
                        description="operator SSH access (ubuntu + root via key)",
                    ),
                ],
                egress=[
                    aws.ec2.SecurityGroupEgressArgs(
                        protocol="-1",
                        from_port=0,
                        to_port=0,
                        cidr_blocks=["0.0.0.0/0"],
                        description="all outbound",
                    ),
                ],
                tags={
                    "Name": f"inferia-worker-sg-{pool_id}",
                    "InferiaPoolId": pool_id,
                },
            )
            kwargs["vpc_security_group_ids"] = [sg.id]
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


def build_azure_vm_program(
    *,
    pool_id: str,
    org_id: str,
    bootstrap_id: str,
    vm_size: str,
    location: str,
    user_data: str,
) -> Callable[[], None]:
    """Return a Pulumi program for a single azure_native VM.

    Creates a resource group, virtual network, subnet, NIC, and the VM.
    user_data is base64-encoded and passed as custom_data (cloud-init).
    """

    def _program() -> None:
        import base64
        import pulumi
        import pulumi_azure_native as azure

        rg = azure.resources.ResourceGroup(
            f"inferia-rg-{pool_id}",
            resource_group_name=f"inferia-rg-{pool_id}",
            location=location,
        )
        vnet = azure.network.VirtualNetwork(
            f"inferia-vnet-{pool_id}",
            resource_group_name=rg.name,
            location=location,
            address_space=azure.network.AddressSpaceArgs(address_prefixes=["10.0.0.0/16"]),
        )
        subnet = azure.network.Subnet(
            f"inferia-subnet-{pool_id}",
            resource_group_name=rg.name,
            virtual_network_name=vnet.name,
            address_prefix="10.0.1.0/24",
        )
        nic = azure.network.NetworkInterface(
            f"inferia-nic-{pool_id}",
            resource_group_name=rg.name,
            location=location,
            ip_configurations=[azure.network.NetworkInterfaceIPConfigurationArgs(
                name="ipconfig",
                subnet=azure.network.SubnetArgs(id=subnet.id),
                private_ip_allocation_method=azure.network.IPAllocationMethod.DYNAMIC,
            )],
        )
        vm = azure.compute.VirtualMachine(
            f"inferia-vm-{pool_id}",
            resource_group_name=rg.name,
            location=location,
            hardware_profile=azure.compute.HardwareProfileArgs(vm_size=vm_size),
            network_profile=azure.compute.NetworkProfileArgs(
                network_interfaces=[azure.compute.NetworkInterfaceReferenceArgs(
                    id=nic.id, primary=True,
                )],
            ),
            os_profile=azure.compute.OSProfileArgs(
                computer_name=f"inferia-{pool_id[:8]}",
                admin_username="azureuser",
                custom_data=base64.b64encode(user_data.encode()).decode(),
                linux_configuration=azure.compute.LinuxConfigurationArgs(
                    disable_password_authentication=False,
                ),
            ),
            storage_profile=azure.compute.StorageProfileArgs(
                image_reference=azure.compute.ImageReferenceArgs(
                    publisher="Canonical",
                    offer="0001-com-ubuntu-server-jammy",
                    sku="22_04-lts-gen2",
                    version="latest",
                ),
            ),
            tags={
                "InferiaPoolId": pool_id,
                "InferiaOrgId": org_id,
                "InferiaBootstrapId": bootstrap_id,
            },
        )
        pulumi.export("vm_id", vm.id)

    return _program
