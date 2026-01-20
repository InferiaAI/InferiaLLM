import sky
from sky import Task, Resources
import uuid
import asyncio
import time
import os
import logging
from typing import List, Dict, Optional

from services.adapter_engine.base import ProviderAdapter

logger = logging.getLogger(__name__)


class SkyPilotAdapter(ProviderAdapter):
    """
    SkyPilot adapter for cloud providers (AWS, GCP, Azure, etc.).
    Stateless infrastructure adapter - no DB access, no side effects beyond provisioning.
    """

    ADAPTER_TYPE = "cloud"

    def __init__(self):
        self.workdir = os.getcwd()

    # -------------------------------------------------
    # DISCOVERY
    # -------------------------------------------------
    async def discover_resources(self) -> List[Dict]:
        """
        Discover available GPU resources from SkyPilot-supported clouds.
        Returns a list of normalized resources.
        """
        # SkyPilot supports multiple clouds; return common GPU types
        # In production, you could query `sky.check` for available resources
        try:
            loop = asyncio.get_running_loop()
            # Get enabled clouds
            enabled_clouds = await loop.run_in_executor(None, sky.check.get_cloud_credential_file_mounts)
            
            # Return common GPU resources available across clouds
            common_gpus = [
                {"gpu_type": "A100", "gpu_memory_gb": 80, "vcpu": 12, "ram_gb": 85},
                {"gpu_type": "A100-80GB", "gpu_memory_gb": 80, "vcpu": 12, "ram_gb": 85},
                {"gpu_type": "A10G", "gpu_memory_gb": 24, "vcpu": 4, "ram_gb": 16},
                {"gpu_type": "V100", "gpu_memory_gb": 16, "vcpu": 8, "ram_gb": 61},
                {"gpu_type": "T4", "gpu_memory_gb": 16, "vcpu": 4, "ram_gb": 16},
                {"gpu_type": "L4", "gpu_memory_gb": 24, "vcpu": 8, "ram_gb": 32},
                {"gpu_type": "H100", "gpu_memory_gb": 80, "vcpu": 26, "ram_gb": 200},
            ]

            resources = []
            for gpu in common_gpus:
                resources.append({
                    "provider": "skypilot",
                    "provider_resource_id": gpu["gpu_type"],
                    "gpu_type": gpu["gpu_type"],
                    "gpu_count": 1,
                    "gpu_memory_gb": gpu["gpu_memory_gb"],
                    "vcpu": gpu["vcpu"],
                    "ram_gb": gpu["ram_gb"],
                    "region": "auto",  # SkyPilot auto-selects region
                    "pricing_model": "on_demand",
                    "price_per_hour": 0.0,  # Varies by cloud/region
                    "metadata": {
                        "clouds": ["aws", "gcp", "azure"],
                    },
                })

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
    ) -> Dict:
        """
        Provision a compute node via SkyPilot.
        """
        task_name = f"skypilot-{uuid.uuid4().hex[:8]}"

        task = (
            Task(
                name=task_name,
                run="echo READY",
                workdir=self.workdir,
            )
            .set_resources(
                Resources(
                    cloud="aws",
                    accelerators={provider_resource_id: 1},
                    use_spot=use_spot,
                    region=region,
                )
            )
        )

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: sky.launch(task))

        instance_data = await self._wait_for_instance(task_name, timeout=300)

        return {
            "provider": "skypilot",
            "provider_instance_id": instance_data["instance_id"],
            "instance_type": provider_resource_id,
            "gpu_total": 1,
            "vcpu_total": instance_data.get("vcpu", 8),
            "ram_gb_total": instance_data.get("ram_gb", 32),
            "region": instance_data.get("region", region),
            "node_class": "spot" if use_spot else "on_demand",
            "metadata": {
                "task_name": task_name,
                "instance_type": instance_data.get("instance_type"),
                "zone": instance_data.get("zone"),
            },
        }

    # -------------------------------------------------
    # DEPROVISION
    # -------------------------------------------------
    async def deprovision_node(self, *, provider_instance_id: str) -> None:
        """
        Deprovision a SkyPilot cluster.
        """
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: sky.down(provider_instance_id))
        except Exception:
            logger.exception("SkyPilot deprovision error")
            raise

    # -------------------------------------------------
    # HELPERS
    # -------------------------------------------------
    def status(self, task_name: str):
        return sky.status(task_name)

    async def _wait_for_instance(self, task_name: str, timeout=600):
        """Wait for SkyPilot cluster to be UP."""
        start = time.time()
        loop = asyncio.get_running_loop()

        while True:
            records = await loop.run_in_executor(
                None, lambda: sky.status(cluster_names=[task_name])
            )

            s = records[0] if records else None

            if s and s.get("status") == sky.ClusterStatus.UP:
                handle = s.get("handle")
                r = s.get("resources")
                return {
                    "instance_id": handle.cluster_name if handle else task_name,
                    "instance_type": r.instance_type if r else None,
                    "vcpu": r.cpus if r else 8,
                    "ram_gb": r.memory if r else 32,
                    "region": getattr(handle, "region", None) if handle else None,
                    "zone": getattr(handle, "zone", None) if handle else None,
                }

            if time.time() - start > timeout:
                raise RuntimeError(f"SkyPilot provisioning timeout for {task_name}")

            await asyncio.sleep(10)
