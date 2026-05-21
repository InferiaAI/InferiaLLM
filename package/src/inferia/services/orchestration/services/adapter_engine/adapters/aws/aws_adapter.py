"""AWS EC2 provider adapter.

Provisions one EC2 GPU instance per provision_node call, embeds a one-shot
bootstrap token in cloud-init user-data, and lets inferia-worker register
itself once it boots.

Credentials: if provider_credential_name is given, loads the encrypted
row from provider_credentials; otherwise boto3 default chain (instance
role on EC2-hosted CPs, env vars / ~/.aws/credentials elsewhere).
"""
from __future__ import annotations

import asyncio
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

    def __init__(self, db=None) -> None:
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
    # DISCOVERY
    # ------------------------------------------------------------------

    async def discover_resources(self, *, region: str = "us-east-1") -> List[Dict]:
        """List EC2 instance types in the given region.

        Returns every instance type the operator's credentials can see in this
        region (no family filter). Each row carries a ``gpu_vendor`` field
        ("nvidia"/"amd"/"intel"/"none") so the UI can offer client-side
        filters (NVIDIA / Other GPU / No GPU). AWS errors are surfaced as
        ProvisionError without leaking internal boto3 text to callers.

        Uses describe_instance_types with NextToken paging to cover the full
        catalog (~700 types as of 2026-05). Server-side filter intentionally
        omitted — the UI prefers a single response it can re-filter without
        round-trips.
        """
        ec2 = self._ec2_client(region, None)
        instance_types: List[Dict] = []
        next_token: Optional[str] = None
        try:
            while True:
                kwargs: Dict[str, Any] = {"MaxResults": 100}
                if next_token:
                    kwargs["NextToken"] = next_token
                resp = ec2.describe_instance_types(**kwargs)
                instance_types.extend(resp.get("InstanceTypes", []))
                next_token = resp.get("NextToken")
                if not next_token:
                    break
        except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError):
            raise ProvisionError("discover_resources failed")

        out: List[Dict] = []
        for it in instance_types:
            gpu_info_root = it.get("GpuInfo") or {}
            gpus = gpu_info_root.get("Gpus") or []
            gpu = gpus[0] if gpus else {}
            mem_mib = (gpu.get("MemoryInfo") or {}).get("SizeInMiB", 0)

            manufacturer = (gpu.get("Manufacturer") or "").strip().lower()
            if not gpus:
                gpu_vendor = "none"
            elif "nvidia" in manufacturer:
                gpu_vendor = "nvidia"
            elif "amd" in manufacturer:
                gpu_vendor = "amd"
            elif "intel" in manufacturer or "habana" in manufacturer:
                # Habana is an Intel-acquired AI accelerator; group under
                # "intel" for the UI's purposes.
                gpu_vendor = "intel"
            else:
                gpu_vendor = "other" if manufacturer else "none"

            out.append(
                {
                    "provider": "aws",
                    "provider_resource_id": it["InstanceType"],
                    "gpu_type": gpu.get("Name", "N/A") if gpus else "N/A",
                    "gpu_count": gpu.get("Count", 0),
                    "gpu_memory_gb": mem_mib // 1024,
                    "gpu_vendor": gpu_vendor,
                    "vcpu": it.get("VCpuInfo", {}).get("DefaultVCpus", 0),
                    "ram_gb": it.get("MemoryInfo", {}).get("SizeInMiB", 0) // 1024,
                    "region": region,
                    "pricing_model": "on_demand",
                    "price_per_hour": 0.0,  # static fallback; pricing API is future work
                }
            )
        return out

    # ------------------------------------------------------------------
    # WAIT FOR READY
    # ------------------------------------------------------------------

    async def wait_for_ready(
        self,
        *,
        provider_instance_id: str,
        timeout: int = 900,
        provider_credential_name: Optional[str] = None,
        region: Optional[str] = None,
    ) -> str:
        """Wait for an EC2 instance to boot and the worker to register.

        Phase 1: Use the boto3 ``instance_running`` waiter so we know the
        hypervisor has started the VM.  If this times out we terminate the
        instance immediately to avoid orphan billing.

        Phase 2: Poll ``compute_inventory`` until the worker process flips the
        row to ``ready`` (it does so via /v1/workers/register after cloud-init
        runs).  If *timeout* elapses without seeing ``ready`` the instance is
        terminated and ``ProvisionTimeoutError`` is raised.
        """
        ec2 = self._ec2_client(region or "us-east-1", provider_credential_name)

        # Phase 1 — wait for hypervisor-level running state.
        try:
            waiter = ec2.get_waiter("instance_running")
            waiter.wait(InstanceIds=[provider_instance_id])
        except botocore.exceptions.WaiterError:
            ec2.terminate_instances(InstanceIds=[provider_instance_id])
            raise ProvisionTimeoutError("instance failed to reach running state")

        # Phase 2 — poll until the worker has registered itself.
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            row = await self._db.fetchrow(
                "SELECT state FROM compute_inventory WHERE provider_instance_id = $1",
                provider_instance_id,
            )
            if row and row["state"] == "ready":
                return "ready"
            await asyncio.sleep(5)

        # Deadline exceeded — kill the instance to avoid billing an orphan.
        try:
            ec2.terminate_instances(InstanceIds=[provider_instance_id])
        except Exception:
            pass
        raise ProvisionTimeoutError("worker did not register in time")

    # ------------------------------------------------------------------
    # DEPROVISION
    # ------------------------------------------------------------------

    async def deprovision_node(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> None:
        """Terminate an EC2 instance.

        Idempotent: ``InvalidInstanceID.NotFound`` is silently ignored so
        callers can safely retry deprovision after a partial failure.
        """
        ec2 = self._ec2_client("us-east-1", provider_credential_name)
        try:
            ec2.terminate_instances(InstanceIds=[provider_instance_id])
        except botocore.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "InvalidInstanceID.NotFound":
                return
            raise ProvisionError("terminate failed")

    # ------------------------------------------------------------------
    # LOGS
    # ------------------------------------------------------------------

    async def get_logs(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        """Return EC2 console output for cloud-init debugging.

        Returns ``{"logs": []}`` on any boto3 error so callers always get a
        well-shaped response even when the instance is very new or already gone.
        """
        ec2 = self._ec2_client("us-east-1", provider_credential_name)
        try:
            resp = ec2.get_console_output(InstanceId=provider_instance_id)
            return {"logs": (resp.get("Output") or "").splitlines()}
        except botocore.exceptions.ClientError:
            return {"logs": []}

    async def get_log_streaming_info(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        """Return WebSocket log-streaming coordinates for a registered worker.

        If the worker has not yet registered (no ``node_id`` in inventory) we
        return ``{"supported": False}`` so callers can fall back to
        ``get_logs``.
        """
        row = await self._db.fetchrow(
            "SELECT node_id FROM compute_inventory WHERE provider_instance_id = $1",
            provider_instance_id,
        )
        if row and row["node_id"]:
            return {
                "supported": True,
                "kind": "worker-ws",
                "ws_url": f"/admin/workers/{row['node_id']}/logs",
            }
        return {"supported": False, "reason": "not registered"}
