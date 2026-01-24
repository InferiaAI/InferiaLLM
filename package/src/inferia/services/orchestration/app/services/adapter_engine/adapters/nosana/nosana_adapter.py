import os
import uuid
import secrets
import logging
from typing import List, Dict, Optional
import aiohttp

from services.adapter_engine.base import ProviderAdapter
from services.adapter_engine.adapters.nosana.job_builder import build_job_definition, create_training_job, NOSANA_INTERNAL_API_KEY

logger = logging.getLogger(__name__)

NOSANA_SIDECAR_URL = os.getenv(
    "NOSANA_SIDECAR_URL",
    "http://localhost:3000/nosana"
)


def generate_api_key(prefix: str = "nos") -> str:
    """Generate a secure API key for Caddy authentication."""
    return f"{prefix}_{secrets.token_urlsafe(32)}"


class NosanaAdapter(ProviderAdapter):
    """
    Nosana DePIN adapter.

    IMPORTANT CONTRACT:
    - provider_resource_id = Nosana MARKET SLUG (e.g. nosana-rtx3060)
    - metadata["image"]    = Docker image to run (from model registry)
    - metadata["api_key"]  = Optional API key for Caddy auth (auto-generated if not provided)
    """

    ADAPTER_TYPE = "depin"
    
    # Simple in-memory cache
    _resources_cache: List[Dict] = []
    _last_discovery_time: float = 0
    CACHE_DURATION: int = 300  # 5 minutes

    # -------------------------------------------------
    # DISCOVER
    # -------------------------------------------------
    async def discover_resources(self) -> List[Dict]:
        mode = os.getenv("NOSANA_MODE", "simulation")

        # if mode == "simulation":
        #     return [
        #         {
        #             "provider": "nosana",
        #             "provider_resource_id": "nosana-rtx3060",
        #             "gpu_type": "RTX3060",
        #             "gpu_count": 1,
        #             "gpu_memory_gb": 12,
        #             "vcpu": 8,
        #             "ram_gb": 32,
        #             "region": "global",
        #             "pricing_model": "fixed",
        #             "price_per_hour": 0.25,
        #             "metadata": {"mode": "simulation"},
        #         }
        #     ]

        import time
        if self._resources_cache and (time.time() - self._last_discovery_time) < self.CACHE_DURATION:
            return self._resources_cache

        url = "https://dashboard.k8s.prd.nos.ci/api/markets"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.error("Nosana discovery failed: %s", resp.status)
                        return []

                    markets = await resp.json()
                    resources = []

                    for m in markets:
                        gpu_types = m.get("gpu_types") or []
                        gpu_type = gpu_types[0] if gpu_types else "unknown"

                        raw_price = m.get("usd_reward_per_hour")
                        price = float(raw_price) if raw_price else 0.0
                        vram = m.get("lowest_vram") or 0

                        resources.append(
                            {
                                "provider": "nosana",
                                "provider_resource_id": m["slug"],   # MARKET ID
                                "gpu_type": gpu_type,
                                "gpu_count": 1,
                                "gpu_memory_gb": vram,
                                "vcpu": 8,
                                "ram_gb": 32,
                                "region": "global",
                                "pricing_model": "fixed",
                                "price_per_hour": price,
                                "metadata": {
                                    "market_address": m["address"],
                                    "mode": "real",
                                },
                            }
                        )

                    
                    self._resources_cache = resources
                    self._last_discovery_time = time.time()
                    return resources

        except Exception:
            logger.exception("Nosana resource discovery error")
            return []

    # -------------------------------------------------
    # PROVISION
    # -------------------------------------------------
    async def provision_node(
        self,
        *,
        provider_resource_id: str,   # Nosana MARKET slug
        pool_id: str,                # Nosana market address
        region: Optional[str] = None,
        use_spot: bool = False,
        metadata: Optional[Dict] = None,
    ) -> Dict:

        if not metadata or "image" not in metadata:
            raise ValueError(
                "NosanaAdapter requires metadata['image'] (docker image)"
            )

        image = metadata["image"]
        gpu_allocated = metadata.get("gpu_allocated", 1)
        vcpu_allocated = metadata.get("vcpu_allocated", 8)
        ram_gb_allocated = metadata.get("ram_gb_allocated", 32)
        
        # Extract model config for job builder
        model_id = metadata.get("model_id") or metadata.get("model_name")
        engine = metadata.get("engine", "vllm")
        hf_token = metadata.get("hf_token") or metadata.get("env", {}).get("HF_TOKEN")

        mode = os.getenv("NOSANA_MODE", "simulation")

        # --- Auto-Resolve Slug to Address (Fix for legacy pools) ---
        if len(pool_id) < 30 or "-" in pool_id:
            logger.warning(f"Pool ID '{pool_id}' looks like a slug. Attempting to resolve to Market Address...")
            try:
                resources = await self.discover_resources()
                found = next((r for r in resources if r["provider_resource_id"] == pool_id), None)
                if found and found.get("metadata", {}).get("market_address"):
                    resolved_addr = found["metadata"]["market_address"]
                    logger.info(f"Resolved slug '{pool_id}' to address '{resolved_addr}'")
                    pool_id = resolved_addr
                else:
                    logger.warning(f"Could not resolve slug '{pool_id}' to an address. Proceeding with raw ID...")
            except Exception as e:
                logger.error(f"Failed to resolve slug during provision: {e}")

        # ---------- BUILD JOB DEFINITION ----------
        # Use job_builder if model_id is provided (new path with API key security)
        
        workload_type = metadata.get("workload_type", "inference")
        
        if workload_type == "training":
            logger.info(f"Building TRAINING job for pool {pool_id}")
            job_definition = create_training_job(
                image=image, # Required in metadata for training
                training_script=metadata.get("training_script"),
                git_repo=metadata.get("git_repo"),
                dataset_url=metadata.get("dataset_url"),
                base_model=metadata.get("base_model"),
                hf_token=hf_token,
                api_key=NOSANA_INTERNAL_API_KEY,
                min_vram=metadata.get("min_vram", 24),
                gpu_count=gpu_allocated,
            )
        
        elif model_id:
            logger.info(f"Using job_builder for engine={engine}, model={model_id}")
            
            # Extract additional config from metadata
            job_config = {
                "gpu_util": metadata.get("gpu_util", 0.95),
                "dtype": metadata.get("dtype", "auto"),
                "enforce_eager": metadata.get("enforce_eager", False),
                "max_model_len": metadata.get("max_model_len", 8192),
                "max_num_seqs": metadata.get("max_num_seqs", 256),
                "enable_chunked_prefill": metadata.get("enable_chunked_prefill", False),
                "quantization": metadata.get("quantization"),
                "min_vram": metadata.get("min_vram", 12),
            }
            
            job_definition = build_job_definition(
                engine=engine,
                model_id=model_id,
                image=image,
                hf_token=hf_token,
                api_key=NOSANA_INTERNAL_API_KEY,  # Global API key for security
                **job_config
            )
            
            logger.debug(f"Built job definition with API key: {bool(NOSANA_INTERNAL_API_KEY)}")
        else:
            # Legacy fallback: Use metadata directly (no API key security)
            logger.warning("No model_id in metadata, using legacy job definition without API key")
            cmd = metadata.get("cmd", [])
            expose = metadata.get("expose", [])
            env = metadata.get("env", {})
            gpu = metadata.get("gpu", True)
            
            job_definition = {
                "version": "0.1",
                "type": "container",
                "meta": {
                    "trigger": "dashboard",
                    "system_requirements": {
                        "required_cuda": ["11.8", "12.1", "12.2", "12.3", "12.4", "12.5", "12.6", "12.8", "12.9"],
                        "required_vram": 12
                    }
                },
                "ops": [
                    {
                        "type": "container/run",
                        "id": "legacy-container",
                        "args": {
                            "image": image,
                            "cmd": cmd,
                            "gpu": gpu,
                            "expose": expose,
                            "env": env,
                        },
                    }
                ],
            }

        # ---------- BUILD PAYLOAD ----------
        payload = {
            "jobDefinition": job_definition,
            "marketAddress": pool_id,
            "resources_allocated": {
                "gpu_allocated": gpu_allocated,
                "vcpu_allocated": vcpu_allocated,
                "ram_gb_allocated": ram_gb_allocated,
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{NOSANA_SIDECAR_URL}/jobs/launch",
                    json=payload,
                ) as resp:

                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(
                            f"Nosana provision failed: {text}"
                        )

                    data = await resp.json()
                    job_address = data["jobAddress"]

                    return {
                        "provider": "nosana",
                        "provider_instance_id": job_address,
                        "hostname": f"nosana-{job_address[-6:]}",
                        "gpu_total": gpu_allocated,
                        "vcpu_total": vcpu_allocated,
                        "ram_gb_total": ram_gb_allocated,
                        "region": "global",
                        "node_class": "fixed",
                        "metadata": {
                            "mode": "real",
                            "job_address": job_address,
                            "image": image,
                            "tx": data.get("txSignature"),
                        },
                    }

        except Exception:
            logger.exception("Nosana provision error")
            raise

    # -------------------------------------------------
    # DEPROVISION
    # -------------------------------------------------
    async def deprovision_node(self, *, provider_instance_id: str) -> None:
        # if os.getenv("NOSANA_MODE", "simulation") == "simulation":
        #     return

        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{NOSANA_SIDECAR_URL}/jobs/stop",
                    json={"jobAddress": provider_instance_id},
                )
        except Exception:
            logger.exception("Nosana deprovision error")
            raise

    # -------------------------------------------------
    # LOGS
    # -------------------------------------------------
    async def get_logs(self, *, provider_instance_id: str) -> Dict:
        """
        Fetch logs from the Nosana sidecar (IPFS result).
        """
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{NOSANA_SIDECAR_URL}/jobs/{provider_instance_id}/logs"
                ) as resp:
                    if resp.status != 200:
                         logger.error("Nosana log fetch failed: %s", resp.status)
                         return {"logs": ["Failed to fetch logs from sidecar"]}
                    
                    data = await resp.json()
                    # data = { status: "completed"|"pending", result: {...}, logs: [...] }
                    
                    if data.get("status") == "pending":
                        return {"logs": data.get("logs", ["Job is running..."])}
                    
                    # If completed, extract logs from result
                    # Nosana IPFS result structure varies, but let's assume raw text or "logs" field
                    result = data.get("result", {})
                    logs = result if isinstance(result, list) else [result]
                    
                    # Try to find a specific logs field if result is dict
                    if isinstance(result, dict):
                         if "logs" in result:
                             logs = result["logs"]
                         elif "stdout" in result:
                             logs = result["stdout"]
                    
                    return {"logs": logs}
        except Exception as e:
            logger.exception("Nosana get_logs error")
            return {"logs": [f"Internal error fetching logs: {str(e)}"]}

    async def get_log_streaming_info(self, *, provider_instance_id: str) -> Dict:
        """
        Returns WebSocket connection details for Nosana log streaming.
        """
        try:
            # 1. Get job details from sidecar to get the node address
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{NOSANA_SIDECAR_URL}/jobs/{provider_instance_id}") as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"Failed to fetch job details: {await resp.text()}")
                    
                    job_data = await resp.json()
                    node_address = job_data.get("nodeAddress")
                    job_state = job_data.get("jobState")
                    
                    # 2=COMPLETED, 3=STOPPED in Nosana
                    is_finished = job_state in (2, 3, 4, "COMPLETED", "STOPPED")
                    
                    if not node_address and not is_finished:
                        raise RuntimeError("Job does not have a node assigned yet")

            # 2. Return the sidecar WS URL and subscription params
            # We use the sidecar as a proxy for the logs
            ws_url = NOSANA_SIDECAR_URL.replace("http://", "ws://").replace("https://", "wss://")
            
            return {
                "ws_url": ws_url,
                "provider": "nosana",
                "subscription": {
                    "type": "subscribe_logs",
                    "provider": "nosana",
                    "jobId": provider_instance_id,
                    "nodeAddress": node_address or "none"
                }
            }
        except Exception as e:
            logger.exception("Nosana get_log_streaming_info error")
            return {"error": str(e)}


# {
#   "version": "0.1",
#   "type": "container",
#   "meta": {
#     "trigger": "dashboard",
#     "system_requirements": {
#       "required_cuda": [
#         "11.8",
#         "12.1",
#         "12.2",
#         "12.3",
#         "12.4",
#         "12.5",
#         "12.6",
#         "12.8",
#         "12.9"
#       ],
#       "required_vram": 16
#     }
#   },
#   "ops": [
#     {
#       "id": "meta-llama/Meta-Llama-3-8B-Instruct",
#       "args": {
#         "cmd": [
#           "--model",
#           "meta-llama/Meta-Llama-3-8B-Instruct",
#           "--served-model-name",
#           "meta-llama/Meta-Llama-3-8B-Instruct",
#           "--port",
#           "9000",
#           "--max-model-len",
#           "8192",
#           "--gpu-memory-utilization",
#           "0.96",
#           "--max-num-seqs",
#           "256",
#           "--dtype",
#           "auto",
#           "--trust-remote-code"
#         ],
#         "env": {
#           "HF_TOKEN": "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"
#         },
#         "gpu": true,
#         "image": "docker.io/vllm/vllm-openai:latest",
#         "expose": [
#           {
#             "port": 9000,
#             "health_checks": [
#               {
#                 "body": "{\"model\": \"meta-llama/Meta-Llama-3-8B-Instruct\", \"messages\": [{\"role\": \"user\", \"content\": \"Respond with a single word: Ready\"}], \"stream\": false}",
#                 "path": "/v1/chat/completions",
#                 "type": "http",
#                 "method": "POST",
#                 "headers": {
#                   "Content-Type": "application/json"
#                 },
#                 "continuous": false,
#                 "expected_status": 200
#               }
#             ]
#           }
#         ]
#       },
#       "type": "container/run"
#     }
#   ]
# }

# for vllm you should use this