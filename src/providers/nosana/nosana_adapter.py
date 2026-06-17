import os
import secrets
import logging
import asyncio
from typing import List, Dict, Optional
import aiohttp
import time

from orchestration.provisioning.engine.base import (
    ProviderAdapter,
    AdapterType,
    PricingModel,
    ProviderCapabilities,
)
from providers.nosana.job_builder import (
    build_job_definition,
    create_training_job,
    INTERNAL_API_KEY,
)
from orchestration.config import settings

logger = logging.getLogger(__name__)

TERMINAL_JOB_STATES = {2, 3, 4, "COMPLETED", "STOPPED", "FAILED", "QUIT"}

# Normalize mixed int/string job states from Nosana API to a canonical string
_STATE_NAME_MAP = {
    0: "QUEUED",
    1: "RUNNING",
    2: "COMPLETED",
    3: "STOPPED",
    4: "QUIT",
}


def _normalize_job_state(raw_state) -> str:
    """Convert Nosana job state (int or string) to a canonical string."""
    if isinstance(raw_state, int):
        return _STATE_NAME_MAP.get(raw_state, f"UNKNOWN({raw_state})")
    return str(raw_state).upper() if raw_state else "UNKNOWN"

# Standard headers for internal sidecar calls
internal_headers = {
    "X-Internal-API-Key": settings.internal_api_key,
    "Content-Type": "application/json",
}


# Configuration with defaults
NOSANA_SIDECAR_URL = settings.nosana_sidecar_url
NOSANA_DISCOVERY_URL = getattr(
    settings, "nosana_discovery_url", "https://dashboard.k8s.prd.nos.ci/api/markets"
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

    ADAPTER_TYPE = AdapterType.DEPIN

    CAPABILITIES = ProviderCapabilities(
        supports_log_streaming=True,
        supports_confidential_compute=True,
        supports_spot_instances=False,
        supports_multi_gpu=True,
        is_ephemeral=True,  # DePIN nodes are externally managed
        requires_readiness_poll=True,
        readiness_timeout_seconds=300,
        polling_interval_seconds=20,
        # Nosana exposes a public node URL (…​.node.k8s.prd.nos.ci) reachable
        # from the control plane, so gate RUNNING on the endpoint actually
        # serving — not just on the job being scheduled (avoids the dashboard
        # showing RUNNING while the model is still pulling/loading and the
        # endpoint 503s). Cold start = 9GB image pull + model load.
        endpoint_http_probeable=True,
        endpoint_ready_timeout_seconds=1800,
        requires_sidecar=True,
        supports_direct_provisioning=True,
        pricing_model=PricingModel.FIXED,
        features={
            "job_based": True,
            "market_based_pricing": True,
            "blockchain_backed": True,
        },
    )

    CACHE_DURATION: int = 300  # 5 minutes

    def __init__(self) -> None:
        self._resources_cache: List[Dict] = []
        self._last_discovery_time: float = 0.0

    # -------------------------------------------------
    # DISCOVER
    # -------------------------------------------------
    async def discover_resources(self) -> List[Dict]:
        if (
            self._resources_cache
            and (time.time() - self._last_discovery_time) < self.CACHE_DURATION
        ):
            return self._resources_cache

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    NOSANA_DISCOVERY_URL,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
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
                        # nodes is a list of currently-online operators; UI
                        # uses this to flag markets where a deploy will fail
                        # the scheduler-assignment step.
                        online_nodes = len(m.get("nodes") or [])

                        resources.append(
                            {
                                "provider": "nosana",
                                "provider_resource_id": m["slug"],  # MARKET ID
                                "gpu_type": gpu_type,
                                "gpu_count": 1,
                                "gpu_memory_gb": vram,
                                "vcpu": 8,
                                "ram_gb": 32,
                                "region": "global",
                                "pricing_model": self.CAPABILITIES.pricing_model.value,
                                "price_per_hour": price,
                                "online_nodes": online_nodes,
                                "metadata": {
                                    "market_address": m["address"],
                                    "mode": "real",
                                    "online_nodes": online_nodes,
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
        provider_resource_id: str,  # Nosana MARKET slug
        pool_id: str,  # Nosana market address
        region: Optional[str] = None,
        use_spot: bool = False,
        metadata: Optional[Dict] = None,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        if not metadata:
            raise ValueError("NosanaAdapter requires deployment metadata")

        image = metadata.get("image")
        gpu_allocated = metadata.get("gpu_allocated", 1)
        vcpu_allocated = metadata.get("vcpu_allocated", 8)
        ram_gb_allocated = metadata.get("ram_gb_allocated", 32)

        # Extract model config for job builder
        model_id = (
            metadata.get("model_id")
            or metadata.get("modelId")
            or metadata.get("model_name")
            or metadata.get("inference_model")
        )
        engine = metadata.get("engine", "vllm")
        hf_token = metadata.get("hf_token") or metadata.get("env", {}).get("HF_TOKEN")

        # --- Auto-Resolve Slug to Address (Fix for legacy pools) ---
        if len(pool_id) < 30 or "-" in pool_id:
            logger.warning(
                f"Pool ID '{pool_id}' looks like a slug. Attempting to resolve to Market Address..."
            )
            try:
                resources = await self.discover_resources()
                found = next(
                    (r for r in resources if r["provider_resource_id"] == pool_id), None
                )
                if found and found.get("metadata", {}).get("market_address"):
                    resolved_addr = found["metadata"]["market_address"]
                    logger.info(
                        f"Resolved slug '{pool_id}' to address '{resolved_addr}'"
                    )
                    pool_id = resolved_addr
                else:
                    logger.warning(
                        f"Could not resolve slug '{pool_id}' to an address. Proceeding with raw ID..."
                    )
            except Exception as e:
                logger.error(f"Failed to resolve slug during provision: {e}")

        # ---------- BUILD JOB DEFINITION ----------
        workload_type = metadata.get("workload_type", "inference")

        if workload_type == "training" and not image:
            raise ValueError("Nosana training deployments require metadata['image']")

        if workload_type == "training":
            logger.info(f"Building TRAINING job for pool {pool_id}")
            job_definition = create_training_job(
                image=image,
                training_script=str(metadata.get("training_script") or ""),
                git_repo=metadata.get("git_repo"),
                dataset_url=metadata.get("dataset_url"),
                base_model=metadata.get("base_model"),
                hf_token=hf_token,
                api_key=INTERNAL_API_KEY,
                min_vram=metadata.get("min_vram", 24),
                gpu_count=gpu_allocated,
            )

        elif model_id:
            logger.info(f"Using job_builder for engine={engine}, model={model_id}")

            # Extract additional config from metadata.
            # Default 0.80 (not 0.95): community/DePIN GPUs reserve VRAM for the CUDA
            # context + co-tenants, and vLLM ABORTS at startup if free < gpu_util*total.
            # 0.95 reliably failed the free-memory check on real nodes. See create_vllm_job.
            job_config = {
                "gpu_util": metadata.get("gpu_util", 0.80),
                "dtype": metadata.get("dtype", "auto"),
                "enforce_eager": metadata.get("enforce_eager", False),
                "max_model_len": metadata.get("max_model_len", 8192),
                "max_num_seqs": metadata.get("max_num_seqs", 256),
                "quantization": metadata.get("quantization"),
                "min_vram": metadata.get("min_vram", 12),
                # Embedding / Generic Engine Config
                "port": metadata.get("port"),
                "batch_size": metadata.get("batch_size", 32),
                "max_batch_tokens": metadata.get("max_batch_tokens", 16384),
                "pooling": metadata.get("pooling", "cls"),
                "gpu": metadata.get("gpu", engine not in ["infinity", "tei"]),
                "required_cpu": metadata.get("required_cpu", 2),
                "required_ram": metadata.get("required_ram", 4096),
                # Additional config
                "trust_remote_code": metadata.get("trust_remote_code", True),
                "cuda_module_loading": metadata.get("cuda_module_loading", "LAZY"),
                "nvidia_disable_cuda_compat": metadata.get(
                    "nvidia_disable_cuda_compat", "1"
                ),
                "kv_cache_dtype": metadata.get("kv_cache_dtype", "auto"),
                # CUDA versions from dashboard
                "required_cuda": metadata.get("required_cuda"),
                # LocalAI / Image generation config
                "threads": metadata.get("threads"),
                "context_size": metadata.get("context_size"),
                "image_path": metadata.get("image_path"),
                "diffusers_pipeline": metadata.get("diffusers_pipeline"),
                "scheduler": metadata.get("scheduler"),
            }

            job_definition = build_job_definition(
                engine=engine,
                model_id=model_id,
                image=image,
                hf_token=hf_token,
                api_key=metadata.get("api_key") or INTERNAL_API_KEY,
                **job_config,
            )
        else:
            logger.warning(
                "No model_id in metadata, using legacy job definition without API key"
            )
            if not image:
                raise ValueError(
                    "Nosana legacy job definition requires metadata['image']"
                )
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
                        "required_cuda": [
                            "11.8",
                            "12.1",
                            "12.2",
                            "12.3",
                            "12.4",
                            "12.5",
                            "12.6",
                            "12.8",
                            "12.9",
                        ],
                        "required_vram": 12,
                    },
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
            "confidential": True,  # Keep job definition private via Deployments API
            "resources_allocated": {
                "gpu_allocated": gpu_allocated,
                "vcpu_allocated": vcpu_allocated,
                "ram_gb_allocated": ram_gb_allocated,
            },
        }

        # Include credential name if specified (for multi-credential support)
        if provider_credential_name:
            payload["credentialName"] = provider_credential_name
            logger.info(f"Using named credential: {provider_credential_name}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{NOSANA_SIDECAR_URL}/nosana/jobs/launch",
                    json=payload,
                    headers=internal_headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(f"Nosana provision failed: {text}")

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
                            "provider_credential_name": provider_credential_name,
                        },
                    }

        except Exception:
            logger.exception("Nosana provision error")
            raise

    async def wait_for_ready(
        self,
        *,
        provider_instance_id: str,
        timeout: int = 300,
        provider_credential_name: Optional[str] = None,
    ) -> str:
        """Polls Nosana sidecar until the job is RUNNING."""
        import time

        capabilities = self.get_capabilities()
        start = time.monotonic()
        poll_interval = capabilities.polling_interval_seconds

        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    params = {}
                    if provider_credential_name:
                        params["credentialName"] = provider_credential_name

                    async with session.get(
                        f"{NOSANA_SIDECAR_URL}/nosana/jobs/{provider_instance_id}",
                        params=params,
                        headers=internal_headers,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status == 200:
                            job = await resp.json()
                            raw_state = job.get("jobState")
                            job_state = _normalize_job_state(raw_state)

                            if raw_state in TERMINAL_JOB_STATES or job_state in TERMINAL_JOB_STATES:
                                raise RuntimeError(
                                    f"Nosana job {provider_instance_id} entered terminal state: {job_state}"
                                )

                            # RUNNING
                            if raw_state == 1 or job_state == "RUNNING":
                                url = job.get("serviceUrl")
                                if url:
                                    return url
                                logger.info(
                                    f"Job {provider_instance_id} is RUNNING. Marking ready."
                                )
                                return "job-running-confidential"
                except RuntimeError:
                    raise
                except Exception as e:
                    logger.warning(f"Error polling Nosana readiness: {e}")

                if time.monotonic() - start > timeout:
                    raise RuntimeError(f"Nosana job {provider_instance_id} timed out")

                await asyncio.sleep(poll_interval)

    async def get_node_status(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> str:
        """One-shot poll of the Nosana job's current state (normalized).

        Returns ``RUNNING`` / ``QUEUED`` / ``COMPLETED`` / ``STOPPED`` /
        ``QUIT`` etc. Returns ``"unknown"`` on any error or non-200 so the
        liveness reconciler never fails a deployment on a transient hiccup —
        it acts ONLY on a confirmed terminal state.
        """
        try:
            params = {}
            if provider_credential_name:
                params["credentialName"] = provider_credential_name
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{NOSANA_SIDECAR_URL}/nosana/jobs/{provider_instance_id}",
                    params=params,
                    headers=internal_headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        return "unknown"
                    job = await resp.json()
                    return _normalize_job_state(job.get("jobState"))
        except Exception:
            logger.warning(
                "Nosana get_node_status failed for %s",
                provider_instance_id,
                exc_info=True,
            )
            return "unknown"

    async def get_node_details(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        """One-shot poll of the full Nosana job record for the Instance Details
        tab. Returns the useful live fields (normalized job state + node /
        deployment / run addresses, service URL, price). Returns ``{}`` on any
        error or non-200 so the read endpoint degrades gracefully (never raises).
        """
        try:
            params = {}
            if provider_credential_name:
                params["credentialName"] = provider_credential_name
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{NOSANA_SIDECAR_URL}/nosana/jobs/{provider_instance_id}",
                    params=params,
                    headers=internal_headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        return {}
                    job = await resp.json()
                    return {
                        "job_state": _normalize_job_state(job.get("jobState")),
                        "node_address": job.get("nodeAddress"),
                        "deployment_address": job.get("deploymentId"),
                        "run_address": job.get("runAddress"),
                        "service_url": job.get("serviceUrl"),
                        "price": job.get("price"),
                        "market": job.get("market") or job.get("marketAddress"),
                    }
        except Exception:
            logger.warning(
                "Nosana get_node_details failed for %s",
                provider_instance_id,
                exc_info=True,
            )
            return {}

    async def deprovision_node(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> None:
        try:
            payload = {"jobAddress": provider_instance_id}
            if provider_credential_name:
                payload["credentialName"] = provider_credential_name

            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{NOSANA_SIDECAR_URL}/nosana/jobs/stop",
                    json=payload,
                    headers=internal_headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                )
        except Exception:
            logger.exception("Nosana deprovision error")
            raise

    async def get_logs(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        try:
            params = {}
            if provider_credential_name:
                params["credentialName"] = provider_credential_name

            async with aiohttp.ClientSession() as session:
                # First try fetching logs directly
                async with session.get(
                    f"{NOSANA_SIDECAR_URL}/nosana/jobs/{provider_instance_id}/logs",
                    params=params,
                    headers=internal_headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("status") == "pending":
                            return {"logs": data.get("logs", ["Job is running..."])}

                        result = data.get("result", {})
                        logs = result if isinstance(result, list) else [result]
                        if isinstance(result, dict):
                            logs = result.get("logs", result.get("stdout", [result]))

                        if logs:
                            return {"logs": logs}

                # If direct logs failed or returned empty, check if job is finished
                # and fetch via the job status endpoint (which includes IPFS result for completed jobs)
                async with session.get(
                    f"{NOSANA_SIDECAR_URL}/nosana/jobs/{provider_instance_id}",
                    params=params,
                    headers=internal_headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        job_data = await resp.json()
                        raw_state = job_data.get("jobState")
                        normalized = _normalize_job_state(raw_state)
                        is_finished = raw_state in TERMINAL_JOB_STATES or normalized in TERMINAL_JOB_STATES

                        if is_finished:
                            return {
                                "logs": [
                                    f"Job finished with state: {normalized}. "
                                    "Nosana does not retain a job's container "
                                    "logs after it ends — open the live Logs "
                                    "view while a deployment is RUNNING to "
                                    "capture engine output (model-load progress, "
                                    "crash/OOM messages, etc.)."
                                ],
                                "job_state": normalized,
                                "source": "job_status",
                            }

                return {"logs": ["Failed to fetch logs"]}
        except Exception:
            logger.exception("Nosana get_logs error")
            return {"logs": ["Internal error fetching logs"]}

    async def get_log_streaming_info(
        self,
        *,
        provider_instance_id: str,
        provider_credential_name: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Dict:
        try:
            params = {}
            if provider_credential_name:
                params["credentialName"] = provider_credential_name

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{NOSANA_SIDECAR_URL}/nosana/jobs/{provider_instance_id}",
                    params=params,
                    headers=internal_headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"Failed to fetch job details")
                    job_data = await resp.json()
                    node_address = job_data.get("nodeAddress")
                    raw_state = job_data.get("jobState")
                    normalized = _normalize_job_state(raw_state)
                    is_finished = raw_state in TERMINAL_JOB_STATES or normalized in TERMINAL_JOB_STATES

                    if not node_address and not is_finished:
                        raise RuntimeError("Job does not have a node assigned yet")

            # When requested via gateway, return relative URL for proxy
            # Otherwise return direct sidecar URL
            if base_url:
                ws_url = "/api/v1/deployment/ws"
            else:
                ws_url = NOSANA_SIDECAR_URL.replace("http://", "ws://").replace(
                    "https://", "wss://"
                )

            info = {
                "ws_url": ws_url,
                "provider": "nosana",
                "subscription": {
                    "type": "subscribe_logs",
                    "provider": "nosana",
                    "jobId": provider_instance_id,
                    "nodeAddress": node_address or "none",
                },
            }
            if provider_credential_name:
                info["subscription"]["credentialName"] = provider_credential_name
            return info
        except Exception as e:
            logger.warning(f"Nosana get_log_streaming_info warning: {e}")
            return {"error": str(e)}
