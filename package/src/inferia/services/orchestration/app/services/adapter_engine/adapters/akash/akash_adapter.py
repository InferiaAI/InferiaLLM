import os
import logging
import aiohttp
from typing import List, Dict, Optional

from services.adapter_engine.base import ProviderAdapter
from services.adapter_engine.adapters.akash.sdl_builder import build_inference_sdl, build_training_sdl

logger = logging.getLogger(__name__)

AKASH_SIDECAR_URL = os.getenv("AKASH_SIDECAR_URL", "http://localhost:3000/akash")

class AkashAdapter(ProviderAdapter):
    """
    Akash Network Adapter.
    Interacts with the Akash Sidecar to deploy containers via SDL.
    """
    
    ADAPTER_TYPE = "depin"

    async def discover_resources(self) -> List[Dict]:
        """
        For Akash, resources are "infinite" in a sense (market based).
        We return a generic resource that represents the Akash Network.
        """
        # In a real implementation, we could query the network stats to see avg prices
        return [
            {
                "provider": "akash",
                "provider_resource_id": "akash-gpu-market",
                "gpu_type": "Various",
                "gpu_count": 0, # Dynamic
                "gpu_memory_gb": 0,
                "vcpu": 0,
                "ram_gb": 0,
                "region": "global",
                "pricing_model": "auction",
                "price_per_hour": 0.0,
                "metadata": {
                    "mode": "real"
                }
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
    ) -> Dict:
        metadata = metadata or {}
        
        workload_type = metadata.get("workload_type", "inference")
        image = metadata.get("image")
        
        # Standardize Resource Keys (Nosana parity)
        gpu_units = int(metadata.get("gpu_allocated") or metadata.get("gpu_count", 1))
        cpu_units = float(metadata.get("vcpu_allocated") or metadata.get("vcpu", 4.0))
        memory_gb = float(metadata.get("ram_gb_allocated") or metadata.get("ram_gb", 16))
        
        # Extract Advanced Features
        command = metadata.get("command") or metadata.get("cmd")
        args = metadata.get("args")
        env = metadata.get("env", {})
        volumes = metadata.get("volumes", [])
        gpu_model = metadata.get("gpu_model", "*") # e.g. "rtxa6000" or "h100"
        
        # Auto-configure SHM (vital for vLLM/PyTorch)
        shm_size = metadata.get("shm_size")
        if shm_size and not any(v.get("mount") == "/dev/shm" for v in volumes):
             volumes.append({
                 "name": "shm",
                 "mount": "/dev/shm", 
                 "size": shm_size,
                 "type": "ram",
                 "readOnly": False
             })

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
                memory_size=f"{int(memory_gb)}Gi"
            )
        else:
            # Inference & General Purpose
            service_name = metadata.get("service_name", "app")
            sdl_content = build_inference_sdl(
                image=image or "vllm/vllm-openai:latest",
                service_name=service_name,
                env=env,
                command=command,
                args=args,
                volumes=volumes,
                gpu_units=gpu_units,
                gpu_model=gpu_model,
                cpu_units=cpu_units,
                memory_size=f"{int(memory_gb)}Gi"
            )

        logger.info(f"Generated SDL for Akash deployment (workload={workload_type}, gpu={gpu_model})")
        
        deployment_id = f"dseq-{os.urandom(4).hex()}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{AKASH_SIDECAR_URL}/deployments/create",
                    json={
                        "sdl": sdl_content,
                        "metadata": metadata
                    }
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
                        "region": "global",
                        "node_class": "dynamic",
                        "expose_url": real_expose_url or f"http://{deployment_id}.akash-provider.com:80", 
                        "metadata": {
                            "lease_id": lease_id,
                            "manifest_sent": True,
                            "workload_type": workload_type,
                            "sdl": sdl_content # Store SDL for debugging
                        }
                    }

        except Exception as e:
            logger.exception("Akash provision error")
            raise e

    async def deprovision_node(
        self,
        *,
        provider_instance_id: str
    ) -> None:
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{AKASH_SIDECAR_URL}/deployments/close",
                    json={"deploymentId": provider_instance_id}
                )
        except Exception:
            logger.exception("Akash deprovision error")
            raise
