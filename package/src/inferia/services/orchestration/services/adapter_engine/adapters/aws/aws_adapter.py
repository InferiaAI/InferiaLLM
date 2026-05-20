"""AWS EC2 provider adapter.

Provisions one EC2 GPU instance per provision_node call, embeds a one-shot
bootstrap token in cloud-init user-data, and lets inferia-worker register
itself once it boots.

Credentials: if provider_credential_name is given, loads the encrypted
row from provider_credentials; otherwise boto3 default chain (instance
role on EC2-hosted CPs, env vars / ~/.aws/credentials elsewhere).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

import boto3
import botocore.exceptions

from inferia.services.orchestration.config import settings
from inferia.services.orchestration.services.adapter_engine.adapters.aws.bootstrap_builder import (
    build_user_data,
)
from inferia.services.orchestration.services.adapter_engine.base import (
    AdapterType,
    PricingModel,
    ProviderAdapter,
    ProviderCapabilities,
)
from inferia.services.orchestration.services.worker_controller.auth import (
    mint_bootstrap_token,
)

logger = logging.getLogger(__name__)


class ProvisionError(Exception):
    """Surface-safe provisioning error (no internal stack text)."""


class ProvisionTimeoutError(ProvisionError):
    """Raised when a node does not become ready within the allowed timeout."""


class AWSAdapter(ProviderAdapter):
    """AWS EC2 cloud provider adapter."""

    ADAPTER_TYPE = AdapterType.CLOUD

    CAPABILITIES = ProviderCapabilities(
        supports_log_streaming=False,
        supports_confidential_compute=False,
        supports_spot_instances=True,
        supports_multi_gpu=True,
        is_ephemeral=False,
        requires_readiness_poll=True,
        readiness_timeout_seconds=600,
        polling_interval_seconds=30,
        requires_sidecar=False,
        supports_direct_provisioning=True,
        pricing_model=PricingModel.ON_DEMAND,
        features={"cloud": "aws", "bootstrap": "cloud-init"},
    )

    def __init__(self, db) -> None:
        self._db = db
        self._sessions: dict[str, boto3.Session] = {}

    # ------------------------------------------------------------------
    # boto3 client helpers
    # ------------------------------------------------------------------

    def _session(self, credential_name: Optional[str]) -> boto3.Session:
        key = credential_name or "__default__"
        if key in self._sessions:
            return self._sessions[key]
        # TODO[future]: load encrypted creds from provider_credentials table
        # when credential_name is not None.  For the first iteration, the
        # operator uses an instance role or AWS_* env vars.
        sess = boto3.Session()
        self._sessions[key] = sess
        return sess

    def _ec2_client(self, region: str, credential_name: Optional[str]):
        return self._session(credential_name).client("ec2", region_name=region)

    def _ssm_client(self, region: str, credential_name: Optional[str]):
        return self._session(credential_name).client("ssm", region_name=region)

    # ------------------------------------------------------------------
    # PROVISION
    # ------------------------------------------------------------------

    async def provision_node(
        self,
        *,
        provider_resource_id: str,
        pool_id: str,
        region: Optional[str] = None,
        use_spot: bool = False,
        metadata: Optional[Dict] = None,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        region = region or "us-east-1"
        metadata = metadata or {}
        pool_uuid = UUID(str(pool_id))

        # ------------------------------------------------------------------
        # Look up pool metadata for subnet / SG / AMI / optional IAM profile.
        # Table is compute_pools (plural).
        # ------------------------------------------------------------------
        pool = await self._db.fetchrow(
            "SELECT id, org_id, metadata FROM compute_pools WHERE id = $1",
            pool_uuid,
        )
        if pool is None:
            raise ProvisionError("pool not found")

        pool_meta: dict = pool["metadata"] or {}
        subnet_id = pool_meta.get("subnet_id")
        security_group_ids = pool_meta.get("security_group_ids")
        if not subnet_id or not security_group_ids:
            raise ProvisionError(
                "pool missing subnet_id or security_group_ids in metadata"
            )

        ami_id: Optional[str] = pool_meta.get("ami_id")
        iam_profile: Optional[str] = pool_meta.get("iam_instance_profile")
        root_gb = int(pool_meta.get("root_volume_gb", 100))
        image_tag = pool_meta.get("worker_image_tag", settings.worker_image_tag)
        org_id: str = pool["org_id"]

        ec2 = self._ec2_client(region, provider_credential_name)

        # ------------------------------------------------------------------
        # Resolve AMI via SSM Parameter Store if not pinned on the pool.
        # ------------------------------------------------------------------
        if ami_id is None:
            ssm = self._ssm_client(region, provider_credential_name)
            try:
                ami_id = ssm.get_parameter(
                    Name=(
                        "/aws/service/deeplearning/ami/x86_64/"
                        "oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
                    )
                )["Parameter"]["Value"]
            except botocore.exceptions.ClientError as exc:
                logger.warning("DLAMI SSM lookup failed: %s", exc)
                raise ProvisionError("AMI lookup failed") from None

        # ------------------------------------------------------------------
        # Mint bootstrap token + RunInstances inside one transaction so a
        # boto3 failure rolls back the token row.
        # ------------------------------------------------------------------
        async with self._db.transaction():
            token, bootstrap_id = await mint_bootstrap_token(
                self._db,
                pool_id=pool_uuid,
                org_id=org_id,
                ttl_seconds=settings.bootstrap_token_ttl_seconds,
            )

            user_data = build_user_data(
                bootstrap_token=token,
                control_plane_url=settings.control_plane_external_url,
                node_name=f"node-{str(bootstrap_id)[:8]}",
                pool_id=str(pool_uuid),
                image=settings.worker_image,
                image_tag=image_tag,
            )

            run_kwargs: Dict[str, Any] = {
                "InstanceType": provider_resource_id,
                "ImageId": ami_id,
                "MinCount": 1,
                "MaxCount": 1,
                "SubnetId": subnet_id,
                "SecurityGroupIds": list(security_group_ids),
                "UserData": user_data,
                "BlockDeviceMappings": [
                    {
                        "DeviceName": "/dev/sda1",
                        "Ebs": {"VolumeSize": root_gb, "VolumeType": "gp3"},
                    }
                ],
                "TagSpecifications": [
                    {
                        "ResourceType": "instance",
                        "Tags": [
                            {
                                "Key": "Name",
                                "Value": f"inferia-worker-{str(bootstrap_id)[:8]}",
                            },
                            {
                                "Key": "InferiaBootstrapId",
                                "Value": str(bootstrap_id),
                            },
                            {
                                "Key": "InferiaPoolId",
                                "Value": str(pool_uuid),
                            },
                            {
                                "Key": "InferiaOrgId",
                                "Value": str(org_id),
                            },
                        ],
                    }
                ],
            }
            if iam_profile:
                run_kwargs["IamInstanceProfile"] = {"Arn": iam_profile}
            if use_spot:
                run_kwargs["InstanceMarketOptions"] = {"MarketType": "spot"}

            try:
                resp = ec2.run_instances(**run_kwargs)
            except (
                botocore.exceptions.ClientError,
                botocore.exceptions.BotoCoreError,
            ) as exc:
                logger.warning("RunInstances failed: %s", exc)
                # Transaction rolls back the bootstrap token row.
                raise ProvisionError("RunInstances failed") from None
            except Exception as exc:
                logger.warning("RunInstances unexpected error: %s", exc)
                raise ProvisionError("RunInstances failed") from None

            instance = resp["Instances"][0]
            instance_id = instance["InstanceId"]
            private_ip = instance.get("PrivateIpAddress", "")
            az = instance.get("Placement", {}).get("AvailabilityZone")

            # Write an inventory row in 'provisioning' state. The worker
            # flips it to 'ready' when it calls /v1/workers/register.
            inventory_meta = {
                "bootstrap_id": str(bootstrap_id),
                "region": region,
                "availability_zone": az,
            }
            await self._db.execute(
                """
                INSERT INTO compute_inventory
                  (pool_id, provider, provider_instance_id, state, metadata)
                VALUES ($1, 'aws', $2, 'provisioning', $3::jsonb)
                """,
                pool_uuid,
                instance_id,
                json.dumps(inventory_meta),
            )

        return {
            "provider": "aws",
            "provider_instance_id": instance_id,
            "hostname": private_ip,
            "gpu_total": 0,     # filled in by worker once it heartbeats
            "vcpu_total": 0,
            "ram_gb_total": 0,
            "region": region,
            "node_class": "spot" if use_spot else "on_demand",
            "metadata": {"bootstrap_id": str(bootstrap_id)},
        }

    # ------------------------------------------------------------------
    # STUBS — Task 15 fills these in
    # ------------------------------------------------------------------

    async def discover_resources(self, *args, **kwargs) -> List[Dict]:
        raise NotImplementedError("Task 15")

    async def wait_for_ready(
        self,
        *,
        provider_instance_id: str,
        timeout: int = 300,
        provider_credential_name: Optional[str] = None,
    ) -> str:
        raise NotImplementedError("Task 15")

    async def deprovision_node(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> None:
        raise NotImplementedError("Task 15")

    async def get_logs(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        raise NotImplementedError("Task 15")

    async def get_log_streaming_info(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        raise NotImplementedError("Task 15")
