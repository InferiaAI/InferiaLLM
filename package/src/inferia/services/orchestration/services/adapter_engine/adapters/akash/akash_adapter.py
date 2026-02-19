import os
import logging
import aiohttp
from typing import List, Dict, Optional
import asyncio

from inferia.services.orchestration.services.adapter_engine.base import (
    ProviderAdapter,
    AdapterType,
    PricingModel,
    ProviderCapabilities,
)
from inferia.services.orchestration.services.adapter_engine.adapters.akash.sdl_builder import (
    build_inference_sdl,
    build_training_sdl,
)
from inferia.services.orchestration.config import settings

logger = logging.getLogger(__name__)

# Configuration with defaults
AKASH_SIDECAR_URL = getattr(
    settings,
    "akash_sidecar_url",
    os.getenv("AKASH_SIDECAR_URL", "http://localhost:3000/akash"),
)

AKASH_RPC_URL = getattr(
    settings,
    "akash_rpc_url",
    os.getenv("AKASH_NODE", "https://rpc.akash.forbole.com:443"),
)

AKASH_API_URL = getattr(
    settings, "akash_api_url", os.getenv("AKASH_API_URL", "https://api.akashnet.net")
)


class AkashAdapter(ProviderAdapter):
    """
    Akash Network Adapter.
    Interacts with the Akash Sidecar to deploy containers via SDL.
    """

    ADAPTER_TYPE = AdapterType.DEPIN

    CAPABILITIES = ProviderCapabilities(
        supports_log_streaming=False,  # TODO: Implement via sidecar
        supports_confidential_compute=False,
        supports_spot_instances=False,
        supports_multi_gpu=True,
        is_ephemeral=True,  # DePIN deployments are externally managed
        requires_readiness_poll=True,
        readiness_timeout_seconds=600,  # Akash auctions can take longer
        polling_interval_seconds=30,
        requires_sidecar=True,
        supports_direct_provisioning=True,
        pricing_model=PricingModel.AUCTION,
        features={
            "sdl_based": True,
            "auction_based_pricing": True,
            "cosmos_sdk_backed": True,
            "persistent_storage": True,
        },
    )

    async def discover_resources(self) -> List[Dict]:
        """
        Discover Akash network resources by querying the sidecar or network stats.
        """
        try:
            # Try to fetch real network stats from sidecar
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{AKASH_SIDECAR_URL}/network/stats",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        stats = await resp.json()
                        return [
                            {
                                "provider": "akash",
                                "provider_resource_id": "akash-gpu-market",
                                "gpu_type": "Various",
                                "gpu_count": 0,  # Dynamic based on auction
                                "gpu_memory_gb": 0,
                                "vcpu": 0,
                                "ram_gb": 0,
                                "region": "global",
                                "pricing_model": self.CAPABILITIES.pricing_model.value,
                                "price_per_hour": stats.get("avg_price_per_hour", 0.0),
                                "metadata": {
                                    "mode": "real",
                                    "total_providers": stats.get("total_providers", 0),
                                    "available_gpus": stats.get("available_gpus", 0),
                                },
                            }
                        ]
        except Exception as e:
            logger.debug(f"Could not fetch Akash network stats: {e}")

        # Fallback to static resource
        return [
            {
                "provider": "akash",
                "provider_resource_id": "akash-gpu-market",
                "gpu_type": "Various",
                "gpu_count": 0,  # Dynamic
                "gpu_memory_gb": 0,
                "vcpu": 0,
                "ram_gb": 0,
                "region": "global",
                "pricing_model": self.CAPABILITIES.pricing_model.value,
                "price_per_hour": 0.0,
                "metadata": {"mode": "real"},
            }
        ]

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
        metadata = metadata or {}

        workload_type = metadata.get("workload_type", "inference")
        image = metadata.get("image")

        # Standardize Resource Keys (normalized)
        gpu_units = int(metadata.get("gpu_allocated") or metadata.get("gpu_count", 1))
        cpu_units = float(metadata.get("vcpu_allocated") or metadata.get("vcpu", 4.0))
        memory_gb = float(
            metadata.get("ram_gb_allocated") or metadata.get("ram_gb", 16)
        )

        # Extract Advanced Features
        command = metadata.get("command") or metadata.get("cmd")
        args = metadata.get("args")
        env = metadata.get("env", {})
        volumes = metadata.get("volumes", [])
        gpu_model = metadata.get("gpu_model", "*")  # e.g. "rtxa6000" or "h100"

        # Auto-configure SHM (vital for vLLM/PyTorch)
        shm_size = metadata.get("shm_size")
        if shm_size and not any(v.get("mount") == "/dev/shm" for v in volumes):
            volumes.append(
                {
                    "name": "shm",
                    "mount": "/dev/shm",
                    "size": shm_size,
                    "type": "ram",
                    "readOnly": False,
                }
            )

        # Build SDL
        sdl_content = ""
        if workload_type == "training":
            sdl_content = build_training_sdl(
                image=image or "pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime",
                training_script=metadata.get("training_script", ""),
                git_repo=metadata.get("git_repo"),
                dataset_url=metadata.get("dataset_url"),
                gpu_units=gpu_units,
                cpu_units=cpu_units,
                memory_size=f"{int(memory_gb)}Gi",
            )
        else:
            # Inference & General Purpose
            service_name = metadata.get("service_name", "app")
            sdl_content = build_inference_sdl(
                image=image or "docker.io/vllm/vllm-openai:v0.14.0",
                service_name=service_name,
                env=env,
                command=command,
                args=args,
                volumes=volumes,
                gpu_units=gpu_units,
                gpu_model=gpu_model,
                cpu_units=cpu_units,
                memory_size=f"{int(memory_gb)}Gi",
            )

        logger.info(
            f"Generated SDL for Akash deployment (workload={workload_type}, gpu={gpu_model})"
        )

        deployment_id = f"dseq-{os.urandom(4).hex()}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{AKASH_SIDECAR_URL}/deployments/create",
                    json={"sdl": sdl_content, "metadata": metadata},
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(f"Akash provision failed: {text}")

                    data = await resp.json()
                    deployment_id = data.get("deploymentId") or deployment_id
                    lease_id = data.get("leaseId")
                    real_expose_url = data.get("exposeUrl")

                    return {
                        "provider": "akash",
                        "provider_instance_id": deployment_id,
                        "hostname": f"akash-{deployment_id}",
                        "gpu_total": gpu_units,
                        "vcpu_total": cpu_units,
                        "ram_gb_total": memory_gb,
                        "region": region or "global",
                        "node_class": "dynamic",
                        "expose_url": real_expose_url
                        or f"http://{deployment_id}.akash-provider.com:80",
                        "metadata": {
                            "lease_id": lease_id,
                            "manifest_sent": True,
                            "workload_type": workload_type,
                            "sdl": sdl_content,  # Store SDL for debugging
                        },
                    }

        except Exception as e:
            logger.exception("Akash provision error")
            raise e

    async def wait_for_ready(
        self, *, provider_instance_id: str, timeout: int = 600
    ) -> str:
        """
        Poll Akash sidecar until the deployment is ready.
        """
        capabilities = self.get_capabilities()
        start = asyncio.get_event_loop().time()
        poll_interval = capabilities.polling_interval_seconds

        while True:
            state = "unknown"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{AKASH_SIDECAR_URL}/deployments/status/{provider_instance_id}",
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status == 200:
                            status = await resp.json()
                            state = status.get("state", "unknown")

                            if state == "active":
                                expose_url = status.get("exposeUrl")
                                if expose_url:
                                    logger.info(
                                        f"Akash deployment {provider_instance_id} is ready: {expose_url}"
                                    )
                                    return expose_url
                                return "akash-ready"
                            elif state in ["closed", "failed"]:
                                raise RuntimeError(
                                    f"Akash deployment {provider_instance_id} {state}"
                                )

                        logger.debug(
                            f"Akash deployment {provider_instance_id} state: {state}, polling..."
                        )

            except asyncio.TimeoutError:
                logger.warning(
                    f"Timeout polling Akash deployment {provider_instance_id}"
                )
            except Exception as e:
                logger.warning(f"Error polling Akash readiness: {e}")

            if asyncio.get_event_loop().time() - start > timeout:
                raise RuntimeError(
                    f"Akash deployment {provider_instance_id} timed out after {timeout}s"
                )

            await asyncio.sleep(poll_interval)

    async def deprovision_node(self, *, provider_instance_id: str) -> None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{AKASH_SIDECAR_URL}/deployments/close",
                    json={"deploymentId": provider_instance_id},
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(f"Failed to close deployment: {text}")
                    logger.info(f"Closed Akash deployment {provider_instance_id}")
        except Exception:
            logger.exception("Akash deprovision error")
            raise

    async def get_logs(self, *, provider_instance_id: str) -> Dict:
        """
        Fetch logs from Akash deployment.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{AKASH_SIDECAR_URL}/deployments/{provider_instance_id}/logs",
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "logs": data.get("logs", []),
                            "status": data.get("status", "unknown"),
                        }
                    else:
                        text = await resp.text()
                        logger.warning(f"Failed to fetch Akash logs: {text}")
                        return {"logs": ["Failed to fetch logs from provider"]}
        except Exception as e:
            logger.exception("Akash get_logs error")
            return {"logs": [f"Error fetching logs: {str(e)}"]}

    async def get_log_streaming_info(self, *, provider_instance_id: str) -> Dict:
        """
        Returns connection details for WebSocket log streaming.
        TODO: Implement when Akash sidecar supports WebSocket streaming.
        """
        # For now, return empty dict indicating streaming not supported
        # When implemented, this should return WebSocket connection details
        return {
            "ws_url": None,
            "provider": "akash",
            "subscription": None,
            "supported": False,
            "message": "Log streaming not yet implemented for Akash",
        }
