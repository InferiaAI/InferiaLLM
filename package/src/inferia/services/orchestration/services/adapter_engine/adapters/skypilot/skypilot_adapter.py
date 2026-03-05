import sky
from sky import Task, Resources
import uuid
import asyncio
import time
import os
import logging
from typing import List, Dict, Optional

from inferia.services.orchestration.services.adapter_engine.base import ProviderAdapter

logger = logging.getLogger(__name__)


class SkyPilotAdapter(ProviderAdapter):
    """
    SkyPilot adapter for cloud providers (AWS, GCP, Azure, etc.).
    Stateless infrastructure adapter - no DB access, no side effects beyond provisioning.

    Compatible with SkyPilot v0.10.x API:
    - sky.launch/down/status return RequestId (non-blocking)
    - Use sky.stream_and_get() or sky.get() to resolve results
    - Resources uses `infra` (str) instead of `cloud` (Cloud object)
    - Status returns List[StatusResponse] (Pydantic models, not dicts)
    """

    ADAPTER_TYPE = "cloud"

    def __init__(self, cloud: str = "aws"):
        self.cloud = cloud
        self.workdir = os.getcwd()

    # -------------------------------------------------
    # DISCOVERY
    # -------------------------------------------------
    async def discover_resources(self) -> List[Dict]:
        """
        Discover available GPU resources from SkyPilot-supported clouds.
        Returns a list of normalized resources.
        """
        try:
            loop = asyncio.get_running_loop()
            enabled_clouds = await loop.run_in_executor(
                None, sky.check.get_cloud_credential_file_mounts
            )

            common_gpus = [
                {"gpu_type": "A100", "gpu_memory_gb": 80, "vcpu": 12, "ram_gb": 85},
                {
                    "gpu_type": "A100-80GB",
                    "gpu_memory_gb": 80,
                    "vcpu": 12,
                    "ram_gb": 85,
                },
                {"gpu_type": "A10G", "gpu_memory_gb": 24, "vcpu": 4, "ram_gb": 16},
                {"gpu_type": "V100", "gpu_memory_gb": 16, "vcpu": 8, "ram_gb": 61},
                {"gpu_type": "T4", "gpu_memory_gb": 16, "vcpu": 4, "ram_gb": 16},
                {"gpu_type": "L4", "gpu_memory_gb": 24, "vcpu": 8, "ram_gb": 32},
                {"gpu_type": "H100", "gpu_memory_gb": 80, "vcpu": 26, "ram_gb": 200},
            ]

            resources = []
            for gpu in common_gpus:
                resources.append(
                    {
                        "provider": self.cloud,
                        "provider_resource_id": gpu["gpu_type"],
                        "gpu_type": gpu["gpu_type"],
                        "gpu_count": 1,
                        "gpu_memory_gb": gpu["gpu_memory_gb"],
                        "vcpu": gpu["vcpu"],
                        "ram_gb": gpu["ram_gb"],
                        "region": "auto",
                        "pricing_model": "on_demand",
                        "price_per_hour": 0.0,
                        "metadata": {
                            "cloud": self.cloud,
                        },
                    }
                )

            return resources

        except Exception:
            logger.exception("SkyPilot resource discovery error")
            return []

    # -------------------------------------------------
    # PROVISION
    # -------------------------------------------------
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
        """
        Provision a compute node via SkyPilot.
        """
        cluster_name = f"inferia-{uuid.uuid4().hex[:8]}"

        task = Task(
            name=cluster_name,
            run="echo READY",
        ).set_resources(
            Resources(
                infra=f"{self.cloud}/{region}" if region else self.cloud,
                accelerators=f"{provider_resource_id}:1",
                use_spot=use_spot,
            )
        )

        loop = asyncio.get_running_loop()

        # sky.launch returns a RequestId; stream_and_get blocks until done
        request_id = await loop.run_in_executor(
            None,
            lambda: sky.launch(task, cluster_name=cluster_name),
        )
        await loop.run_in_executor(
            None,
            lambda: sky.stream_and_get(request_id),
        )

        instance_data = await self._wait_for_instance(cluster_name, timeout=300)

        return {
            "provider": self.cloud,
            "provider_instance_id": instance_data["instance_id"],
            "hostname": instance_data["instance_id"],
            "instance_type": provider_resource_id,
            "gpu_total": 1,
            "vcpu_total": instance_data.get("vcpu", 8),
            "ram_gb_total": instance_data.get("ram_gb", 32),
            "region": instance_data.get("region", region),
            "node_class": "spot" if use_spot else "on_demand",
            "metadata": {
                "cluster_name": cluster_name,
                "instance_type": instance_data.get("instance_type"),
                "zone": instance_data.get("zone"),
            },
        }

    async def wait_for_ready(
        self,
        *,
        provider_instance_id: str,
        timeout: int = 600,
        provider_credential_name: Optional[str] = None,
    ) -> str:
        """
        Wait until the SkyPilot cluster is UP.
        """
        await self._wait_for_instance(provider_instance_id, timeout=timeout)
        return provider_instance_id

    # -------------------------------------------------
    # DEPROVISION
    # -------------------------------------------------
    async def deprovision_node(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> None:
        """
        Deprovision a SkyPilot cluster.
        """
        try:
            loop = asyncio.get_running_loop()
            request_id = await loop.run_in_executor(
                None, lambda: sky.down(provider_instance_id)
            )
            await loop.run_in_executor(
                None, lambda: sky.stream_and_get(request_id)
            )
        except Exception:
            logger.exception("SkyPilot deprovision error")
            raise

    # -------------------------------------------------
    # HELPERS
    # -------------------------------------------------
    def status(self, cluster_name: str):
        request_id = sky.status(cluster_names=[cluster_name])
        return sky.get(request_id)

    async def _wait_for_instance(self, cluster_name: str, timeout=600):
        """Wait for SkyPilot cluster to be UP."""
        start = time.time()
        loop = asyncio.get_running_loop()

        while True:
            request_id = await loop.run_in_executor(
                None,
                lambda: sky.status(cluster_names=[cluster_name]),
            )
            records = await loop.run_in_executor(
                None,
                lambda: sky.get(request_id),
            )

            s = records[0] if records else None

            if s and s.status == sky.ClusterStatus.UP:
                return {
                    "instance_id": s.name,
                    "instance_type": s.resources_str,
                    "vcpu": int(float(s.cpus)) if s.cpus else 8,
                    "ram_gb": int(float(s.memory)) if s.memory else 32,
                    "region": s.region,
                    "zone": None,
                }

            if time.time() - start > timeout:
                raise RuntimeError(
                    f"SkyPilot provisioning timeout for {cluster_name}"
                )

            await asyncio.sleep(10)

    # -------------------------------------------------
    # LOGS
    # -------------------------------------------------
    async def get_logs(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        """
        Fetch logs from a SkyPilot cluster.
        """
        return {
            "logs": [
                "SkyPilot logs are currently available via CLI: sky logs "
                + provider_instance_id
            ]
        }

    async def get_log_streaming_info(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        """
        Returns info for SkyPilot log streaming.
        """
        return {
            "ws_url": None,
            "provider": self.cloud,
            "subscription": {"cluster_name": provider_instance_id},
        }
