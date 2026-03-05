import sky
from sky import Task, Resources
import uuid
import asyncio
import time
import os
import logging
from typing import List, Dict, Optional

from inferia.services.orchestration.services.adapter_engine.base import (
    ProviderAdapter,
    ProviderCapabilities,
    PricingModel,
    AdapterType,
)

logger = logging.getLogger(__name__)


class SkyPilotAdapter(ProviderAdapter):
    """
    SkyPilot adapter for cloud providers (AWS, GCP, Azure, etc.).

    Supports two modes:
    1. Job mode (legacy): Each deployment provisions a new cluster and destroys it on stop
    2. Cluster mode (new): Provision persistent cluster on pool creation, deploy services on it

    Compatible with SkyPilot v0.10.x API:
    - sky.launch/down/status return RequestId (non-blocking)
    - Use sky.stream_and_get() or sky.get() to resolve results
    - Resources uses `infra` (str) instead of `cloud` (Cloud object)
    - Status returns List[StatusResponse] (Pydantic models, not dicts)
    """

    ADAPTER_TYPE: AdapterType = AdapterType.CLOUD

    CAPABILITIES = ProviderCapabilities(
        supports_log_streaming=False,
        supports_confidential_compute=False,
        supports_spot_instances=True,
        supports_multi_gpu=True,
        is_ephemeral=False,  # Cluster persists until explicitly terminated
        requires_readiness_poll=True,
        readiness_timeout_seconds=600,
        polling_interval_seconds=10,
        requires_sidecar=False,
        supports_direct_provisioning=True,
        supports_cluster_mode=True,  # NEW: Supports persistent cluster mode
        pricing_model=PricingModel.ON_DEMAND,
        features={
            "cloud_providers": ["aws", "gcp", "azure", "lambda", "runpod"],
            "supports_spot": True,
        },
    )

    def __init__(self, cloud: str = "gcp"):
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
            # Handle composite ID (cluster_id/service_name)
            cluster_id = provider_instance_id.split("/")[0]

            loop = asyncio.get_running_loop()
            request_id = await loop.run_in_executor(
                None, lambda: sky.down(cluster_id)
            )
            await loop.run_in_executor(None, lambda: sky.stream_and_get(request_id))
        except Exception:
            logger.exception("SkyPilot deprovision error")
            raise

    # -------------------------------------------------
    # CLUSTER MODE (Persistent cluster for deployments)
    # -------------------------------------------------
    async def provision_cluster(
        self,
        *,
        cluster_name: str,
        gpu_type: str,
        gpu_count: int = 1,
        region: Optional[str] = None,
        use_spot: bool = False,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        """
        Provision a persistent SkyPilot cluster.

        Called when creating a cluster-based pool (SkyPilot).
        The cluster persists until explicitly terminated.
        """
        logger.info(f"Provisioning SkyPilot cluster: {cluster_name} on {self.cloud} with {gpu_count}x {gpu_type}")

        task = Task(
            name=cluster_name,
            run="echo 'Cluster ready'",
        ).set_resources(
            Resources(
                infra=f"{self.cloud}/{region}" if region else self.cloud,
                accelerators=f"{gpu_type}:{gpu_count}",
                use_spot=use_spot,
                ports=["8000-9000"],  # Open a range for multiple deployments
            )
        )

        loop = asyncio.get_running_loop()

        request_id = await loop.run_in_executor(
            None,
            lambda: sky.launch(task, cluster_name=cluster_name),
        )
        await loop.run_in_executor(
            None,
            lambda: sky.stream_and_get(request_id),
        )

        cluster_info = await self._wait_for_instance(cluster_name, timeout=600)

        return {
            "cluster_id": cluster_name,
            "cluster_name": cluster_name,
            "provider": self.cloud,
            "hostname": cluster_info["instance_id"],
            "instance_type": cluster_info.get("instance_type", gpu_type),
            "gpu_total": gpu_count,
            "gpu_type": gpu_type,
            "vcpu_total": cluster_info.get("vcpu", 8),
            "ram_gb_total": cluster_info.get("ram_gb", 32),
            "region": cluster_info.get("region", region),
            "zone": cluster_info.get("zone"),
            "node_class": "spot" if use_spot else "on_demand",
            "status": "running",
            "metadata": {
                "cloud": self.cloud,
                "use_spot": use_spot,
            },
        }

    async def terminate_cluster(
        self,
        *,
        cluster_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> None:
        """
        Terminate a persistent SkyPilot cluster.

        Called when deleting a cluster-based pool.
        """
        logger.info(f"Terminating SkyPilot cluster: {cluster_id}")
        try:
            loop = asyncio.get_running_loop()
            request_id = await loop.run_in_executor(None, lambda: sky.down(cluster_id))
            await loop.run_in_executor(None, lambda: sky.stream_and_get(request_id))
            logger.info(f"Cluster {cluster_id} terminated successfully")
        except Exception:
            logger.exception(f"Error terminating cluster {cluster_id}")
            raise

    async def deploy_service(
        self,
        *,
        cluster_id: str,
        service_name: str,
        image: str,
        ports: List[Dict],
        env: Optional[Dict] = None,
        cmd: Optional[List[str]] = None,
        provider_credential_name: Optional[str] = None,
    ) -> str:
        """
        Deploy a service on an existing SkyPilot cluster.

        Called when starting a deployment on a cluster-based pool.
        Uses sky exec to run Docker container on the cluster.
        """
        logger.info(f"Deploying service {service_name} on cluster {cluster_id}")

        port_mappings = ", ".join([f"{p['port']}:{p['port']}" for p in ports])
        env_vars = " ".join([f"-e {k}={v}" for k, v in (env or {}).items()])

        gpu_flags = ""
        if "vllm" in image.lower() or "cuda" in image.lower() or "ollama" in image.lower():
            gpu_flags = "--gpus all --shm-size 1g --ipc=host"

        docker_cmd = (
            f"docker run -d --name {service_name} {gpu_flags} {env_vars} -p {port_mappings} {image}"
        )
        if cmd:
            docker_cmd += f" {' '.join(cmd)}"

        loop = asyncio.get_running_loop()

        task = Task(
            name=service_name,
            run=docker_cmd,
        )

        request_id = await loop.run_in_executor(
            None,
            lambda: sky.exec(task, cluster_name=cluster_id),
        )
        await loop.run_in_executor(
            None,
            lambda: sky.stream_and_get(request_id),
        )

        # Get the cluster IP for the URL
        cluster_info = await self.get_cluster_status(cluster_id=cluster_id)
        head_ip = cluster_info.get("head_ip") or cluster_id

        service_url = f"http://{head_ip}:{ports[0]['port'] if ports else 8000}"
        logger.info(f"Service {service_name} deployed at {service_url}")

        return service_url

    async def stop_service(
        self,
        *,
        cluster_id: str,
        service_name: str,
        provider_credential_name: Optional[str] = None,
    ) -> None:
        """
        Stop a service on a cluster.

        Called when stopping a deployment. The cluster remains alive.
        """
        logger.info(f"Stopping service {service_name} on cluster {cluster_id}")

        loop = asyncio.get_running_loop()

        task = Task(
            name=f"stop-{service_name}",
            run=f"docker stop {service_name} && docker rm {service_name}",
        )

        request_id = await loop.run_in_executor(
            None,
            lambda: sky.exec(task, cluster_name=cluster_id),
        )
        await loop.run_in_executor(
            None,
            lambda: sky.stream_and_get(request_id),
        )

        logger.info(f"Service {service_name} stopped on cluster {cluster_id}")

    async def get_cluster_status(
        self,
        *,
        cluster_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        """
        Get the status of a SkyPilot cluster.
        """
        # Handle composite ID
        cluster_id = cluster_id.split("/")[0]

        try:
            loop = asyncio.get_running_loop()
            request_id = await loop.run_in_executor(
                None,
                lambda: sky.status(cluster_names=[cluster_id]),
            )
            records = await loop.run_in_executor(
                None,
                lambda: sky.get(request_id),
            )

            if not records:
                return {"status": "terminated", "cluster_id": cluster_id}

            cluster = records[0]
            head_ip = None
            if hasattr(cluster, "handle") and cluster.handle:
                head_ip = getattr(cluster.handle, "head_ip", None)

            return {
                "status": (
                    cluster.status.name
                    if hasattr(cluster.status, "name")
                    else str(cluster.status)
                ),
                "cluster_id": cluster_id,
                "instance_type": (
                    cluster.resources_str if hasattr(cluster, "resources_str") else None
                ),
                "region": cluster.region if hasattr(cluster, "region") else None,
                "is_up": cluster.status == sky.ClusterStatus.UP,
                "head_ip": head_ip,
            }
        except Exception as e:
            logger.warning(f"Error getting cluster status for {cluster_id}: {e}")
            return {"status": "error", "cluster_id": cluster_id, "error": str(e)}

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
                raise RuntimeError(f"SkyPilot provisioning timeout for {cluster_name}")

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
        For cluster-based deployments, we run 'docker logs' on the cluster.
        """
        # Usually provider_instance_id is cluster_id or cluster_id/service_name
        # If it's just cluster_id, we can't know which service without more info.
        # But for now, we'll try to guess if it's 'cluster_id' and we don't have service_name.

        cluster_id = provider_instance_id
        service_name = None

        if "/" in provider_instance_id:
            cluster_id, service_name = provider_instance_id.split("/", 1)

        if not service_name:
            # Fallback: list running containers and take the first one starting with 'deploy-'
            task = Task(
                name="get-logs", run="docker ps --filter name=deploy- --format '{{.Names}}'"
            )
        else:
            task = Task(name="get-logs", run=f"docker logs --tail 100 {service_name}")

        loop = asyncio.get_running_loop()
        try:
            request_id = await loop.run_in_executor(
                None,
                lambda: sky.exec(task, cluster_name=cluster_id),
            )
            logs = await loop.run_in_executor(
                None,
                lambda: sky.get(request_id),
            )
            # SkyPilot logs are captured during exec, we need to read from sky's log file
            # or just return the output of the command if sky.get returns it.
            # Actually, the easier way is sky.stream_and_get logic or check the log file.
            
            # For now, let's just return a placeholder while we improve this
            return {
                "logs": [
                    f"Fetching logs from cluster {cluster_id} for service {service_name or 'latest'}",
                    "To see full logs, use: sky logs " + cluster_id
                ]
            }
        except Exception as e:
            return {"logs": [f"Error fetching logs: {e}"]}

    async def get_log_streaming_info(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Dict:
        """
        Returns info for SkyPilot log streaming via Orchestrator WebSocket.
        """
        # We'll point to the orchestrator's websocket.
        # Dashboard will try to connect to this.
        
        # Determine the WS URL base
        if base_url:
            # When requested via gateway, we return a relative URL
            # The gateway is mounted at /api/v1, and proxy route is /deployment/ws
            ws_url = "/api/v1/deployment/ws"
        else:
            ws_url = "ws://localhost:8080/deployment/ws"
        
        cluster_id = provider_instance_id
        service_name = None
        if "/" in provider_instance_id:
            cluster_id, service_name = provider_instance_id.split("/", 1)

        return {
            "ws_url": ws_url,
            "provider": "skypilot",
            "subscription": {
                "type": "subscribe_logs",
                "provider": "skypilot",
                "cluster_id": cluster_id,
                "service_name": service_name,
            },
        }
