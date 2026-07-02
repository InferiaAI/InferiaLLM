"""
Nosana Job Builder Module

Constructs container job definitions for Nosana DePIN deployments.
Supports vLLM, Ollama, and vLLM-Omni engines.
"""

import logging
from typing import Dict, Any, Optional, List
import json
import os
import re
import shlex
from orchestration.config import settings

logger = logging.getLogger(__name__)

# Internal API key used for service-to-service auth and vLLM security
INTERNAL_API_KEY = settings.internal_api_key or os.getenv("INTERNAL_API_KEY", "")

# CUDA forward-compat fix for the vllm/vllm-openai (and vllm-omni) images.
#
# Those images bake a CUDA forward-compat libcuda (e.g.
# /usr/local/cuda/compat/libcuda.so.575) into their ld.so cache, AHEAD of the
# standard lib dirs. Forward-compat is meant to run a newer CUDA userspace on an
# OLDER kernel driver. On hosts whose NVIDIA driver is NEWER than that compat lib
# (the common case on Nosana GPU nodes, which run recent drivers), the loader
# still binds the baked compat libcuda — now mismatched against the running kernel
# module — and CUDA init dies at vLLM EngineCore startup with
# "Error 803: system has unsupported display driver / cuda driver combination",
# before a single weight is fetched. The container then exits and Nosana reports
# the job COMPLETED while the inference endpoint never serves (HTTP 503).
#
# Pointing LD_LIBRARY_PATH at the dirs where the container runtime injects the
# HOST driver's libcuda (which always matches the running kernel module) makes the
# loader bind the correct driver and bypass the broken compat shim — any
# LD_LIBRARY_PATH entry is searched before the ld.so cache. The injection dir
# varies by host/runtime (Debian multiarch /usr/lib/x86_64-linux-gnu on the AWS
# DLAMI; /usr/lib on others), so we list every common location and let the first
# one that actually contains libcuda win. This mirrors the fix already shipped in
# the inferia-worker vLLM recipe (internal/runtime/recipes/vllm.go), broadened so
# it is robust to whatever layout a given Nosana operator node uses.
#
# NOTE: NVIDIA_DISABLE_CUDA_COMPAT=1 alone does NOT fix this — the compat lib is
# baked into the image's ld.so cache at build time, not added by the runtime
# entrypoint hook that env var gates (verified live: the broken container sets it
# and still hits Error 803).
CUDA_DRIVER_LD_LIBRARY_PATH = (
    "/usr/lib/x86_64-linux-gnu:/usr/lib64:/usr/lib:"
    "/lib/x86_64-linux-gnu:/lib64:/lib:"
    "/usr/local/nvidia/lib64:/usr/local/cuda/lib64"
)

# Nosana's POST /deployments/create validates each job-definition container op
# "id" against ^[A-Za-z0-9_-]+$. Any other character — notably the "/" and "."
# that appear in HuggingFace repo ids such as "Qwen/Qwen3-0.6B" or
# "meta-llama/Llama-3.1-8B-Instruct" — is rejected with an opaque HTTP 400
# ({"error":"Bad Request"}), failing the deploy at submission before a node is
# ever scheduled. (Verified live: "/" AND "." both rejected; uppercase, digits,
# "_" and "-" accepted.) We therefore derive op ids that pass this validator.
_OPID_INVALID_RE = re.compile(r"[^A-Za-z0-9_-]")
_OPID_DASH_RUN_RE = re.compile(r"-{2,}")
# Conservative identifier-length cap (DNS-label size). Truncating only ever
# keeps the slug valid; Nosana's own limit is at least this large.
_OPID_MAX_LEN = 63


def _sanitize_op_id(raw: str, *, fallback: str = "service") -> str:
    """Derive a Nosana-valid container op id from an arbitrary string.

    Maps every character outside ``[A-Za-z0-9_-]`` to ``-`` (so "/" and "." in
    HF repo ids become "-"), collapses runs of dashes, trims stray leading/
    trailing separators, caps the length, and falls back to a static literal if
    nothing valid survives.

    Pure and deterministic — the same input always yields the same slug. This
    matters because the Nosana SDK derives the public service-URL hash from the
    job definition (which embeds the op id), so the id must be stable across
    re-resolves of the same deployment.
    """
    slug = _OPID_INVALID_RE.sub("-", raw or "")
    slug = _OPID_DASH_RUN_RE.sub("-", slug).strip("-_")
    slug = slug[:_OPID_MAX_LEN].strip("-_")
    return slug or fallback


def create_vllm_job(
    model_id: str,
    image: str = "docker.io/vllm/vllm-openai:v0.22.1",
    hf_token: Optional[str] = None,
    api_key: Optional[str] = None,
    # Stability & Hardware
    # 0.80 (not vLLM's 0.95/0.90): community/DePIN GPUs always reserve VRAM for the
    # CUDA context (~0.8 GiB) plus any co-tenant processes, and vLLM ABORTS at startup
    # if free memory < gpu_util * TOTAL ("Free memory on device ... is less than desired
    # GPU memory utilization"). 0.95 demanded ~95% of total be free and reliably failed
    # on real nodes (e.g. 10.81/11.62 GiB free). 0.80 leaves headroom across the fleet
    # (incl. 8 GiB cards) and matches the dashboard's intended default. Overridable.
    gpu_util: float = 0.80,
    dtype: str = "auto",
    enforce_eager: bool = False,
    min_vram: int = 8,
    # Advanced Tuning
    max_model_len: Optional[int] = None,
    max_num_seqs: int = 256,
    quantization: Optional[str] = None,
    # Additional config
    trust_remote_code: bool = True,
    cuda_module_loading: str = "LAZY",
    nvidia_disable_cuda_compat: str = "",
    kv_cache_dtype: str = "auto",
    # System Requirements
    required_cuda: Optional[List[str]] = None,
    # HF Resource Preloading
    hf_preload: bool = True,
) -> Dict[str, Any]:
    """
    Build a Nosana job definition for vLLM inference server.

    Args:
        model_id: HuggingFace model ID (e.g., "meta-llama/Llama-3.1-8B-Instruct")
        image: Docker image for vLLM
        hf_token: HuggingFace token for gated models
        api_key: API key for authentication (uses global key if not provided)
        gpu_util: GPU memory utilization (0.0-1.0)
        dtype: Data type (auto, float16, bfloat16)
        enforce_eager: Disable CUDA graphs for stability
        min_vram: Minimum VRAM requirement in GB
        max_model_len: Maximum context length
        max_num_seqs: Maximum concurrent sequences
        quantization: Quantization method (awq, gptq, etc.)
        trust_remote_code: Trust remote code when loading models
        cuda_module_loading: CUDA module loading strategy (LAZY, EAGER)
        nvidia_disable_cuda_compat: Disable CUDA compatibility layer
        kv_cache_dtype: KV cache data type

    Returns:
        Dict with 'op' (container operation) and 'meta' (job metadata)
    """
    # Use provided key or fall back to global
    effective_api_key = api_key or INTERNAL_API_KEY

    # Construct the Health Check Body
    health_body = json.dumps(
        {
            "model": model_id,
            "messages": [
                {"role": "user", "content": "Respond with a single word: Ready"}
            ],
            "stream": False,
        }
    )

    # Health Check Headers
    health_headers: Dict[str, str] = {"Content-Type": "application/json"}
    if effective_api_key:
        health_headers["Authorization"] = f"Bearer {effective_api_key}"

    # Prepare Environment Variables
    envs: Dict[str, str] = {}

    if cuda_module_loading:
        envs["CUDA_MODULE_LOADING"] = cuda_module_loading

    if nvidia_disable_cuda_compat:
        envs["NVIDIA_DISABLE_CUDA_COMPAT"] = nvidia_disable_cuda_compat

    # Bind the host driver's libcuda instead of the image's baked forward-compat
    # shim, or EngineCore CUDA init dies with Error 803 on newer-driver nodes.
    # See CUDA_DRIVER_LD_LIBRARY_PATH above.
    envs["LD_LIBRARY_PATH"] = CUDA_DRIVER_LD_LIBRARY_PATH

    token_to_use = hf_token or os.getenv("HF_TOKEN")
    if token_to_use:
        envs["HF_TOKEN"] = token_to_use

    # HF Resource Preloading: if enabled and no token is required (the Nosana
    # HF resource loader cannot authenticate against gated repos), the node
    # downloads the model from HuggingFace before the container starts so that
    # vLLM loads from local disk instead of re-downloading at runtime.  This
    # eliminates model-download time from cold start and leverages cross-job
    # node caching (re-deploying the same model on the same market is near-
    # instant).
    resources = None
    effective_model_path = model_id
    if hf_preload:
        if token_to_use:
            logger.warning(
                "hf_preload enabled but hf_token is set — skipping HF resource "
                "preloading (Nosana HF resource loader cannot authenticate). "
                "Model will be downloaded at container runtime as usual."
            )
        else:
            safe_name = model_id.replace("/", "-")
            target_path = f"/data-models/{safe_name}"
            resources = [
                {
                    "type": "HF",
                    "repo": model_id,
                    "target": target_path + "/",
                }
            ]
            effective_model_path = target_path
            logger.info(
                "HF resource preloading enabled: %s -> %s",
                model_id, target_path,
            )

    cmd_args = [
        effective_model_path,
        "--served-model-name",
        model_id,
        "--host",
        "0.0.0.0",
        "--port",
        "9000",
        "--gpu-memory-utilization",
        str(gpu_util),
        "--max-num-seqs",
        str(max_num_seqs),
        "--dtype",
        dtype,
    ]

    # Only pin --max-model-len when it is explicitly known. Forcing a value
    # ABOVE the model's native context (max_position_embeddings) makes vLLM
    # HARD-ERROR at config creation and the container crashes during model load
    # ("User-specified max_model_len (8192) is greater than the derived
    # max_model_len" — e.g. facebook/opt-125m's native 2048). When None, vLLM
    # derives the model's native context, which is always valid.
    if max_model_len is not None:
        cmd_args.extend(["--max-model-len", str(max_model_len)])

    if trust_remote_code:
        cmd_args.append("--trust-remote-code")

    cmd_args.extend(["--kv-cache-dtype", kv_cache_dtype])

    # Add quantization flag if provided
    if quantization:
        cmd_args.extend(["--quantization", quantization])

    # Inject API Key if present
    if effective_api_key:
        cmd_args.extend(["--api-key", effective_api_key])

    # Eager execution
    if enforce_eager:
        cmd_args.append("--enforce-eager")

    container_op = {
        "id": _sanitize_op_id(model_id, fallback="vllm"),
        "type": "container/run",
        "args": {
            "cmd": cmd_args,
            "env": envs,
            "gpu": True,
            "image": image,
            "expose": [
                {
                    "port": 9000,
                    "health_checks": [
                        {
                            "body": health_body,
                            "path": "/v1/chat/completions",
                            "type": "http",
                            "method": "POST",
                            "headers": health_headers,
                            "continuous": False,
                            "expected_status": 200,
                        }
                    ],
                }
            ],
        },
    }

    if resources:
        container_op["args"]["resources"] = resources

    meta_data = {
        "trigger": "dashboard",
        "system_requirements": {
            "required_cuda": required_cuda
            or [
                "12.4",
                "12.8",
                "13.0",
                "13.2",
            ],
            "required_vram": min_vram,
        },
    }

    return {"op": container_op, "meta": meta_data}


def create_ollama_job(
    model_id: str,
    image: str = "docker.io/ollama/ollama:latest",
    api_key: Optional[str] = None,
    min_vram: int = 4,
    required_cuda: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Build a Nosana job definition for Ollama inference server.

    Uses Caddy as a reverse proxy for API key authentication when api_key is provided.

    Args:
        model_id: Ollama model name (e.g., "llama3", "mistral")
        image: Docker image for Ollama
        api_key: API key for authentication (uses global key if not provided)
        min_vram: Minimum VRAM requirement in GB

    Returns:
        Dict with 'op' (container operation) and 'meta' (job metadata)
    """
    # Use provided key or fall back to global
    effective_api_key = api_key or INTERNAL_API_KEY

    ollama_image = image if "ollama" in image else "docker.io/ollama/ollama:latest"
    safe_model_id = shlex.quote(model_id)

    final_cmd: List[str]
    exposed_port: int
    envs: Dict[str, str] = {}

    if effective_api_key:
        # Secure mode: Use Caddy as reverse proxy for authentication
        exposed_port = 8080
        envs["MY_API_KEY"] = effective_api_key

        secure_script = (
            "apt-get update && apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl && "
            "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg && "
            "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list && "
            "apt-get update && apt-get install -y caddy && "
            'printf ":8080 {\\n  @auth {\\n    not header Authorization \\"Bearer %s\\"\\n  }\\n  respond @auth \\"Unauthorized\\" 401\\n  reverse_proxy localhost:11434 {\\n    flush_interval -1\\n  }\\n}" "$MY_API_KEY" > Caddyfile ; '
            "ollama serve & echo 'Waiting for Ollama...' && "
            "while ! curl -s http://localhost:11434 > /dev/null; do sleep 2; done && "
            "echo 'Ollama is ready!' ; "
            "caddy run --config Caddyfile & echo 'Caddy running on :8080' && "
            f"ollama pull {safe_model_id} && "
            "echo 'Model pulled successfully' && wait"
        )
        final_cmd = ["-c", secure_script]
    else:
        # Unsecured mode: Direct Ollama access
        exposed_port = 11434
        final_cmd = [
            "-c",
            f"ollama serve & sleep 5 && ollama pull {safe_model_id} && tail -f /dev/null",
        ]

    container_op = {
        "type": "container/run",
        "id": "ollama-service",
        "args": {
            "image": ollama_image,
            "entrypoint": ["/bin/sh"],
            "cmd": final_cmd,
            "env": envs,
            "gpu": True,
            "expose": [
                {
                    "port": exposed_port,
                }
            ],
        },
    }

    meta_data = {
        "trigger": "dashboard",
        "system_requirements": {
            "required_cuda": required_cuda
            or [
                "12.4",
                "12.8",
                "13.0",
                "13.2",
            ],
            "required_vram": min_vram,
        },
    }

    return {"op": container_op, "meta": meta_data}


def create_vllm_omni_job(
    model_id: str,
    image: str = "docker.io/vllm/vllm-omni:v0.11.0rc1",
    hf_token: Optional[str] = None,
    api_key: Optional[str] = None,
    # Stability & Hardware
    # 0.80 leaves VRAM headroom for the CUDA context + co-tenants on community GPUs;
    # vLLM aborts at startup if free memory < gpu_util * total. See create_vllm_job.
    gpu_util: float = 0.80,
    dtype: str = "auto",
    enforce_eager: bool = False,
    min_vram: int = 16,
    # Advanced Tuning
    max_model_len: Optional[int] = None,
    max_num_seqs: int = 64,
    limit_mm_per_prompt: str = "image=1,video=1",
    required_cuda: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Build a Nosana job definition for vLLM-Omni multimodal inference server.

    Args:
        model_id: HuggingFace model ID for multimodal model
        image: Docker image for vLLM-Omni
        hf_token: HuggingFace token for gated models
        api_key: API key for authentication (uses global key if not provided)
        gpu_util: GPU memory utilization (0.0-1.0)
        dtype: Data type (auto, float16, bfloat16)
        enforce_eager: Disable CUDA graphs for stability
        min_vram: Minimum VRAM requirement in GB
        max_model_len: Maximum context length
        max_num_seqs: Maximum concurrent sequences (lower for multimodal)
        limit_mm_per_prompt: Multimodal input limits

    Returns:
        Dict with 'op' (container operation) and 'meta' (job metadata)
    """
    # Use provided key or fall back to global
    effective_api_key = api_key or INTERNAL_API_KEY

    health_body = json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": "Ready?"}],
            "stream": False,
        }
    )

    health_headers: Dict[str, str] = {"Content-Type": "application/json"}
    if effective_api_key:
        health_headers["Authorization"] = f"Bearer {effective_api_key}"

    envs: Dict[str, str] = {}
    # Bind the host driver's libcuda instead of the image's baked forward-compat
    # shim, or EngineCore CUDA init dies with Error 803 on newer-driver nodes.
    # See CUDA_DRIVER_LD_LIBRARY_PATH above.
    envs["LD_LIBRARY_PATH"] = CUDA_DRIVER_LD_LIBRARY_PATH
    token_to_use = hf_token or os.getenv("HF_TOKEN")
    if token_to_use:
        envs["HF_TOKEN"] = token_to_use

    # vLLM-Omni command args
    cmd_args = [
        model_id,
        "--served-model-name",
        model_id,
        "--host",
        "0.0.0.0",
        "--port",
        "9000",
        "--omni",
        "--gpu-memory-utilization",
        str(gpu_util),
        "--max-num-seqs",
        str(max_num_seqs),
        "--dtype",
        dtype,
        "--trust-remote-code",
    ]

    # See create_vllm_job: pin --max-model-len only when known, else let vLLM
    # derive the native context (forcing > native crashes the container).
    if max_model_len is not None:
        cmd_args.extend(["--max-model-len", str(max_model_len)])

    if enforce_eager:
        cmd_args.append("--enforce-eager")

    if effective_api_key:
        cmd_args.extend(["--api-key", effective_api_key])

    container_op = {
        "id": _sanitize_op_id(f"vllm-omni-{model_id}", fallback="vllm-omni"),
        "type": "container/run",
        "args": {
            "cmd": cmd_args,
            "env": envs,
            "gpu": True,
            "image": image,
            "expose": 9000,
        },
    }

    meta_data = {
        "trigger": "dashboard",
        "system_requirements": {
            "required_cuda": required_cuda
            or [
                "12.4",
                "12.8",
                "13.0",
                "13.2",
            ],
            "required_vram": min_vram,
        },
    }

    return {"op": container_op, "meta": meta_data}


def create_triton_job(
    model_id: str,
    image: str = "nvcr.io/nvidia/tritonserver:23.10-py3",
    api_key: Optional[str] = None,
    min_vram: int = 8,
    required_cuda: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Build a Nosana job definition for NVIDIA Triton Inference Server.

    Args:
        model_id: Model repository path (e.g. s3://bucket/models or /mnt/models)
        image: Triton server image
        api_key: API key for authentication (uses global key if not provided)
        min_vram: Minimum VRAM requirement in GB

    Returns:
        Dict with 'op' (container operation) and 'meta' (job metadata)
    """
    effective_api_key = api_key or INTERNAL_API_KEY

    # Triton default ports
    http_port = 8000
    grpc_port = 8001
    metrics_port = 8002

    exposed_port: int
    final_cmd: List[str]
    envs: Dict[str, str] = {}

    # Base Triton command
    # We use --model-control-mode=explicit (or poll) usually, but default is fine.
    # Essential to point to model repository.
    safe_model_id = shlex.quote(model_id)
    triton_cmd = (
        f"tritonserver --model-repository={safe_model_id} "
        "--disable-auto-complete "
        "--http-port=8000 --grpc-port=8001 --metrics-port=8002"
    )

    if effective_api_key:
        # Secure mode: Use Caddy as reverse proxy
        exposed_port = 8080
        envs["MY_API_KEY"] = effective_api_key

        # Caddy setup script (similar to Ollama but proxying port 8000)
        secure_script = (
            "apt-get update && apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl && "
            "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg && "
            "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list && "
            "apt-get update && apt-get install -y caddy && "
            'printf ":8080 {\\n  @auth {\\n    not header Authorization \\"Bearer %s\\"\\n  }\\n  respond @auth \\"Unauthorized\\" 401\\n  reverse_proxy localhost:8000 {\\n    flush_interval -1\\n  }\\n}" "$MY_API_KEY" > Caddyfile && '
            f"{triton_cmd} & "
            "echo 'Waiting for Triton...' && "
            "while ! curl -s http://localhost:8000/v2/health/ready > /dev/null; do sleep 2; done && "
            "echo 'Triton is ready!' && "
            "caddy run --config Caddyfile"
        )
        final_cmd = ["-c", secure_script]
    else:
        # Unsecured mode
        exposed_port = http_port
        final_cmd = ["-c", triton_cmd]

    container_op = {
        "type": "container/run",
        "id": "triton-service",
        "args": {
            "image": image,
            "entrypoint": ["/bin/sh"],
            "cmd": final_cmd,
            "env": envs,
            "gpu": True,
            "expose": exposed_port,
        },
    }

    meta_data = {
        "trigger": "dashboard",
        "system_requirements": {
            "required_cuda": required_cuda
            or [
                "12.4",
                "12.8",
                "13.0",
                "13.2",
            ],
            "required_vram": min_vram,
        },
    }

    return {"op": container_op, "meta": meta_data}


def create_infinity_job(
    model_id: str,
    image: str = "michaelf34/infinity:latest",
    hf_token: Optional[str] = None,
    api_key: Optional[str] = None,
    port: int = 7997,
    batch_size: int = 32,
    # Hardware
    gpu: bool = False,
    required_cpu: int = 2,
    required_ram: int = 4096,
) -> Dict[str, Any]:
    """
    Build a Nosana job definition for Infinity embedding server.
    Infinity is a high-performance embedding server for sentence-transformers.

    Args:
        model_id: HuggingFace model ID (e.g., "sentence-transformers/all-MiniLM-L6-v2")
        image: Docker image for Infinity
        hf_token: HuggingFace token for gated models
        api_key: API key for authentication
        port: Port to expose
        batch_size: Batch size for embedding requests

    Returns:
        Dict with 'op' (container operation) and 'meta' (job metadata)
    """
    effective_api_key = api_key or INTERNAL_API_KEY

    # Prepare Environment Variables
    envs: Dict[str, str] = {
        "INFINITY_MODEL_ID": model_id,
        "INFINITY_PORT": str(port),
    }

    token_to_use = hf_token or os.getenv("HF_TOKEN")
    if token_to_use:
        envs["HF_TOKEN"] = token_to_use

    # Construct command for Infinity v2 using shell to ensure proper execution
    # The infinity_emb CLI needs to be called with 'v2' subcommand
    safe_model_id = shlex.quote(model_id)
    cmd_str = f"infinity_emb v2 --model-id {safe_model_id} --port {port} --batch-size {batch_size}"

    if effective_api_key:
        cmd_str += f" --api-key {shlex.quote(effective_api_key)}"

    # Keep container alive
    cmd_str += " && tail -f /dev/null"

    container_op = {
        "type": "container/run",
        "id": "infinity-service",
        "args": {
            "image": image,
            "entrypoint": ["/bin/sh"],
            "cmd": ["-c", cmd_str],
            "env": envs,
            "gpu": gpu,
            "expose": port,  # Simple port number format for Nosana
        },
    }

    meta_data = {
        "trigger": "dashboard",
        "system_requirements": {
            "required_cpu": required_cpu,
            "required_ram": required_ram,
        },
    }

    return {"op": container_op, "meta": meta_data}


def create_tei_job(
    model_id: str,
    image: str = "ghcr.io/huggingface/text-embeddings-inference:latest",
    hf_token: Optional[str] = None,
    api_key: Optional[str] = None,
    port: int = 8080,
    max_batch_tokens: int = 16384,
    pooling: str = "cls",
    # Hardware
    gpu: bool = False,
    required_cpu: int = 2,
    required_ram: int = 4096,
) -> Dict[str, Any]:
    """
    Build a Nosana job definition for Hugging Face Text Embeddings Inference (TEI) server.
    TEI is Hugging Face's official high-performance embedding server.

    Args:
        model_id: HuggingFace model ID (e.g., "sentence-transformers/all-MiniLM-L6-v2")
        image: Docker image for TEI
        hf_token: HuggingFace token for gated models
        api_key: API key for authentication
        port: Port to expose
        max_batch_tokens: Maximum number of tokens per batch

    Returns:
        Dict with 'op' (container operation) and 'meta' (job metadata)
    """
    effective_api_key = api_key or INTERNAL_API_KEY

    # Prepare Environment Variables
    envs: Dict[str, str] = {}
    token_to_use = hf_token or os.getenv("HF_TOKEN")
    if token_to_use:
        envs["HF_TOKEN"] = token_to_use

    # Construct command for TEI
    cmd_args = [
        "--model-id",
        model_id,
        "--port",
        str(port),
        "--max-batch-tokens",
        str(max_batch_tokens),
        "--pooling",
        pooling,
    ]

    # Add API key if provided
    if effective_api_key:
        envs["API_KEY"] = effective_api_key
        cmd_args.extend(["--api-key", effective_api_key])

    container_op = {
        "type": "container/run",
        "id": "tei-service",
        "args": {
            "image": image,
            "cmd": cmd_args,
            "env": envs,
            "gpu": gpu,
            "expose": port,  # Simple port number format for Nosana
        },
    }

    meta_data = {
        "trigger": "dashboard",
        "system_requirements": {
            "required_cpu": required_cpu,
            "required_ram": required_ram,
        },
    }

    return {"op": container_op, "meta": meta_data}


def create_localai_job(
    model_id: str,
    image: str = "docker.io/localai/localai:latest-gpu-nvidia-cuda-12",
    hf_token: Optional[str] = None,
    api_key: Optional[str] = None,
    # Hardware
    min_vram: int = 8,
    gpu: bool = True,
    # LocalAI config
    port: int = 8080,
    threads: int = 4,
    context_size: int = 512,
    # Image generation specific
    image_path: str = "/tmp/generated/images",
    diffusers_pipeline: Optional[str] = None,
    scheduler: Optional[str] = None,
    # System Requirements
    required_cuda: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Build a Nosana job definition for InferaDiffusion image generation server.
    Supports Stable Diffusion and other image generation models via LocalAI backend.
    See: https://localai.io/features/image-generation/

    LocalAI can run Stable Diffusion models using the diffusers backend.
    Models are auto-downloaded from HuggingFace when first requested, or
    can be pre-configured via a model gallery YAML.

    Args:
        model_id: HuggingFace model ID (e.g., "stabilityai/stable-diffusion-2-1")
                  or a LocalAI model gallery name
        image: Docker image for InferaDiffusion/LocalAI
        hf_token: HuggingFace token for gated models
        api_key: API key for authentication (uses global key if not provided)
        min_vram: Minimum VRAM requirement in GB
        gpu: Whether to use GPU
        port: Port to expose
        threads: Number of CPU threads
        context_size: Context size
        image_path: Path to store generated images
        diffusers_pipeline: Override diffusers pipeline type
            (e.g. "StableDiffusionPipeline", "StableDiffusionImg2ImgPipeline")
        scheduler: Override scheduler (e.g. "EulerAncestralDiscreteScheduler")

    Returns:
        Dict with 'op' (container operation) and 'meta' (job metadata)
    """
    effective_api_key = api_key or INTERNAL_API_KEY

    # LocalAI container docs: https://localai.io/installation/containers/
    # The official image has its own ENTRYPOINT baked into the Dockerfile.
    # We must NOT override the entrypoint — just pass env vars and let the
    # container start normally.
    #
    # Image generation docs: https://localai.io/features/image-generation/
    # Model config is passed via the MODELS env var as a JSON string, which
    # LocalAI reads on startup to configure backends.

    model_config_name = model_id.replace("/", "--")

    # Build the model config YAML for the diffusers backend.
    # LocalAI loads YAML files from MODELS_PATH on startup.
    yaml_lines = [
        f"name: {model_config_name}",
        "backend: diffusers",
        "parameters:",
        f"  model: {model_id}",
    ]
    if diffusers_pipeline or scheduler:
        yaml_lines.append("diffusers:")
        if diffusers_pipeline:
            yaml_lines.append(f"  pipeline_type: {diffusers_pipeline}")
        if scheduler:
            yaml_lines.append(f"  scheduler_type: {scheduler}")

    yaml_content = "\\n".join(yaml_lines)
    models_path = "/models"

    envs: Dict[str, str] = {
        "MODELS_PATH": models_path,
        "IMAGE_PATH": image_path,
        "THREADS": str(threads),
        "CONTEXT_SIZE": str(context_size),
        "ADDRESS": f"0.0.0.0:{port}",
    }

    token_to_use = hf_token or os.getenv("HF_TOKEN")
    if token_to_use:
        envs["HF_TOKEN"] = token_to_use
        envs["HUGGINGFACEHUB_API_TOKEN"] = token_to_use

    if effective_api_key:
        envs["API_KEY"] = effective_api_key

    # Write the model config YAML to the models directory, then find and
    # exec the local-ai binary (its path varies across image versions).
    cmd_str = (
        f"mkdir -p {models_path} && "
        f"mkdir -p {shlex.quote(image_path)} && "
        f'printf "{yaml_content}" > {models_path}/{model_config_name}.yaml && '
        f'BINARY=$(find / -name "local-ai" -type f -executable 2>/dev/null | head -1) && '
        f'exec "$BINARY"'
    )

    container_op = {
        "type": "container/run",
        "id": "localai-image-service",
        "args": {
            "image": image,
            "entrypoint": ["/bin/bash"],
            "cmd": ["-c", cmd_str],
            "env": envs,
            "gpu": gpu,
            "expose": port,
        },
    }

    meta_data = {
        "trigger": "dashboard",
        "system_requirements": {
            "required_cuda": required_cuda
            or [
                "12.4",
                "12.8",
                "13.0",
                "13.2",
            ],
            "required_vram": min_vram,
        },
    }

    return {"op": container_op, "meta": meta_data}


def create_inferia_diffusion_job(
    model_id: str = "sdxl-turbo",
    image: str = "docker.io/inferiaai/inferiadiffusion:latest",
    api_key: Optional[str] = None,
    hf_token: Optional[str] = None,
    port: int = 8000,
    host: str = "0.0.0.0",
    min_vram: int = 6,
    required_cuda: Optional[List[str]] = None,
    model_type: Optional[str] = None,
    trust_remote_code: bool = False,
    model_offload: bool = False,
    group_offload: bool = False,
    # HF Resource Preloading
    hf_preload: bool = True,
) -> Dict[str, Any]:
    """
    Build a Nosana job definition for Inferia Diffusion engine.

    Args:
        model_id: Model to serve (e.g., "sdxl-turbo", "sd-3.5", "video-model")
        image: Docker image for Inferia Diffusion
        api_key: API key for authentication (uses global key if not provided)
        hf_token: HuggingFace token for private models (e.g., "hf_xxxxx")
        port: Port to expose
        host: Host to bind to
        min_vram: Minimum VRAM requirement in GB
        model_type: image_generation|video_generation|image|video — maps to --model-type
        trust_remote_code: allow remote code execution for the model (--trust-remote-code)
        model_offload: enable sequential model CPU offload (--model-offload)
        group_offload: enable grouped CPU offload (--group-offload)

    Returns:
        Dict with 'op' (container operation) and 'meta' (job metadata)
    """
    effective_api_key = api_key or INTERNAL_API_KEY

    health_headers: Dict[str, str] = {}
    if effective_api_key:
        health_headers["Authorization"] = f"Bearer {effective_api_key}"

    # HF Resource Preloading: only for raw HF model IDs.  Config keys like
    # "sdxl-turbo" (no "/") don't identify a HuggingFace repo, and local
    # paths (start with "/") are already on disk — neither can be preloaded.
    resources = None
    effective_model = model_id
    _is_hf_id = "/" in model_id and not model_id.startswith("/")
    if hf_preload and _is_hf_id:
        if hf_token:
            logger.warning(
                "hf_preload enabled but hf_token is set — skipping HF resource "
                "preloading (Nosana HF resource loader cannot authenticate). "
                "Model will be downloaded at container runtime as usual."
            )
        else:
            safe_name = model_id.replace("/", "-")
            target_path = f"/data-models/{safe_name}"
            resources = [
                {
                    "type": "HF",
                    "repo": model_id,
                    "target": target_path + "/",
                }
            ]
            effective_model = target_path
            logger.info(
                "HF resource preloading enabled: %s -> %s",
                model_id, target_path,
            )

    cmd_args = [
        "inferiadiffusion",
        "serve",
        "--model",
        effective_model,
        "--host",
        host,
        "--port",
        str(port),
    ]

    _MODEL_TYPE_FLAG = {
        "image_generation": "image",
        "image": "image",
        "video_generation": "video",
        "video": "video",
    }
    _mt = _MODEL_TYPE_FLAG.get((model_type or "").lower())
    if _mt:
        cmd_args.extend(["--model-type", _mt])

    if effective_api_key:
        cmd_args.extend(["--api-key", effective_api_key])

    if hf_token:
        cmd_args.extend(["--hf-token", hf_token])

    if trust_remote_code:
        cmd_args.append("--trust-remote-code")
    if model_offload:
        cmd_args.append("--model-offload")
    if group_offload:
        cmd_args.append("--group-offload")

    container_op = {
        "id": "InferiaDiffusion",
        "type": "container/run",
        "args": {
            "cmd": cmd_args,
            "gpu": True,
            "image": image,
            "expose": [
                {
                    "port": port,
                    "health_checks": [
                        {
                            "body": None,
                            "path": "/health",
                            "type": "http",
                            "method": "GET",
                            "headers": health_headers,
                            "continuous": False,
                            "expected_status": 200,
                        }
                    ],
                }
            ],
        },
    }

    if resources:
        container_op["args"]["resources"] = resources

    meta_data = {
        "trigger": "dashboard",
        "system_requirements": {
            "required_cuda": required_cuda or ["12.4", "12.8", "13.0", "13.2"],
            "required_vram": min_vram,
        },
    }

    return {"op": container_op, "meta": meta_data}


def build_job_definition(
    engine: str,
    model_id: str,
    image: Optional[str] = None,
    hf_token: Optional[str] = None,
    api_key: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    High-level factory function to build job definitions based on engine type.

    Args:
        engine: Engine type ("vllm", "ollama", "vllm-omni", "infinity", "triton", "inferia-diffusion")
        model_id: Model identifier (e.g., "sdxl-turbo" for images, "video-model" for videos)
        image: Optional custom docker image
        hf_token: HuggingFace token
        api_key: API key for authentication
        **kwargs: Additional engine-specific arguments

    Returns:
        Complete Nosana job definition ready for posting

    Note:
        InferaDiffusion engine supports both image and video generation via the same image.
        Image models: "sdxl-turbo", "sd-3.5", etc.
        Video models: "video-model", etc.
    """
    if engine == "vllm":
        job = create_vllm_job(
            model_id=model_id,
            image=image or "docker.io/vllm/vllm-openai:v0.22.1",
            hf_token=hf_token,
            api_key=api_key,
            **{
                k: v
                for k, v in kwargs.items()
                if k
                in [
                    "gpu_util",
                    "dtype",
                    "enforce_eager",
                    "min_vram",
                    "max_model_len",
                    "max_num_seqs",
                    "quantization",
                    "trust_remote_code",
                    "cuda_module_loading",
                    "nvidia_disable_cuda_compat",
                    "kv_cache_dtype",
                    "required_cuda",
                    "hf_preload",
                ]
                and v is not None
            },
        )
    elif engine == "ollama":
        job = create_ollama_job(
            model_id=model_id,
            image=image or "docker.io/ollama/ollama:latest",
            api_key=api_key,
            **{
                k: v
                for k, v in kwargs.items()
                if k in ["min_vram", "required_cuda"] and v is not None
            },
        )
    elif engine == "vllm-omni":
        job = create_vllm_omni_job(
            model_id=model_id,
            image=image or "docker.io/vllm/vllm-omni:v0.11.0rc1",
            hf_token=hf_token,
            api_key=api_key,
            **{
                k: v
                for k, v in kwargs.items()
                if k
                in [
                    "gpu_util",
                    "dtype",
                    "enforce_eager",
                    "min_vram",
                    "max_model_len",
                    "max_num_seqs",
                    "limit_mm_per_prompt",
                    "required_cuda",
                ]
                and v is not None
            },
        )
    elif engine == "triton":
        job = create_triton_job(
            model_id=model_id,
            image=image or "nvcr.io/nvidia/tritonserver:23.10-py3",
            api_key=api_key,
            **{
                k: v
                for k, v in kwargs.items()
                if k in ["min_vram", "required_cuda"] and v is not None
            },
        )
    elif engine == "infinity":
        job = create_infinity_job(
            model_id=model_id,
            image=image or "michaelf34/infinity:latest",
            hf_token=hf_token,
            api_key=api_key,
            **{
                k: v
                for k, v in kwargs.items()
                if k in ["port", "batch_size", "gpu", "required_cpu", "required_ram"]
                and v is not None
            },
        )
    elif engine == "tei":
        job = create_tei_job(
            model_id=model_id,
            image=image or "ghcr.io/huggingface/text-embeddings-inference:latest",
            hf_token=hf_token,
            api_key=api_key,
            **{
                k: v
                for k, v in kwargs.items()
                if k
                in [
                    "port",
                    "max_batch_tokens",
                    "pooling",
                    "gpu",
                    "required_cpu",
                    "required_ram",
                ]
                and v is not None
            },
        )
    elif engine in ("inferia-diffusion",):
        job = create_inferia_diffusion_job(
            model_id=model_id,
            image=image or "docker.io/inferiaai/inferiadiffusion:latest",
            api_key=api_key,
            hf_token=hf_token,
            **{
                k: v
                for k, v in kwargs.items()
                if k
                in [
                    "port",
                    "host",
                    "min_vram",
                    "required_cuda",
                    "model_type",
                    "trust_remote_code",
                    "model_offload",
                    "group_offload",
                    "hf_preload",
                ]
                and v is not None
            },
        )
    else:
        raise ValueError(f"Unsupported engine: {engine}")

    # Return full job definition
    return {
        "version": "0.1",
        "type": "container",
        "meta": job["meta"],
        "ops": [job["op"]],
    }


def create_training_job(
    image: str,
    training_script: str,  # This can be a URL or a command string
    git_repo: Optional[str] = None,
    dataset_url: Optional[str] = None,
    base_model: Optional[str] = None,
    hf_token: Optional[str] = None,
    api_key: Optional[str] = None,
    # Hardware
    min_vram: int = 24,  # Training usually needs more
    gpu_count: int = 1,
    required_cuda: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Build a Nosana job definition for a Training Job.

    Args:
        image: Training container image (e.g. pytorch/pytorch...)
        training_script: The command to run or script URL
        git_repo: Optional git repository to clone
        dataset_url: Optional dataset URL to download
        base_model: Base model ID if fine-tuning
        hf_token: HF Token
        api_key: API key
        min_vram: Min VRAM
        gpu_count: Number of GPUs

    Returns:
        Dict with 'op' and 'meta'
    """
    effective_api_key = api_key or INTERNAL_API_KEY

    envs: Dict[str, str] = {}
    token_to_use = hf_token or os.getenv("HF_TOKEN")
    if token_to_use:
        envs["HF_TOKEN"] = token_to_use

    if effective_api_key:
        envs["NOSANA_API_KEY"] = effective_api_key

    if git_repo:
        envs["GIT_REPO"] = git_repo
    if dataset_url:
        envs["DATASET_URL"] = dataset_url
    if base_model:
        envs["BASE_MODEL"] = base_model

    # Construct command
    # We assume the image has an entrypoint that handles GIT_REPO etc,
    # OR we construct a shell command here to do it.
    # For robustness, let's construct a shell command sequence.

    # Sanitize all user-supplied values to prevent command injection
    safe_git_repo = shlex.quote(git_repo) if git_repo else None
    safe_dataset_url = shlex.quote(dataset_url) if dataset_url else None
    safe_training_script = shlex.quote(training_script)

    cmd_parts = []

    # Harden command:
    # 1. Install System Deps (Git is often missing in bare PyTorch container)
    cmd_parts.append("apt-get update && apt-get install -y git wget")

    # 2. Clone Repo
    if git_repo:
        cmd_parts.append(
            f"git clone {safe_git_repo} /workspace/repo && cd /workspace/repo"
        )
        # 3. Install Python Dependencies
        cmd_parts.append(
            "if [ -f requirements.txt ]; then pip install -r requirements.txt; fi"
        )
        # Install common ML libs *with pinned versions* to match the PyTorch 2.1.2 base image
        cmd_parts.append(
            "pip install transformers==4.38.2 datasets==2.18.0 tiktoken wandb"
        )

        # 4. Data Preparation Heuristic (e.g. for nanoGPT)
        # If openwebtext/prepare.py exists, run it to generate train.bin
        cmd_parts.append(
            "if [ -f data/openwebtext/prepare.py ]; then python data/openwebtext/prepare.py; elif [ -f prepare.py ]; then python prepare.py; fi"
        )
    else:
        cmd_parts.append("mkdir -p /workspace && cd /workspace")

    # 4. Download Dataset
    if dataset_url:
        cmd_parts.append(
            f"wget -O dataset.tar.gz {safe_dataset_url} && tar -xvf dataset.tar.gz"
        )

    # 5. Run Script
    # If training_script looks like a file path, python it. If it's a command, run it.
    if training_script.endswith(".py"):
        cmd_parts.append(f"python {safe_training_script}")
    elif training_script.endswith(".sh"):
        cmd_parts.append(f"bash {safe_training_script}")
    else:
        cmd_parts.append(safe_training_script)

    # Wrap the entire chain in a subshell catch to keep container alive on failure for debugging
    # "set -e" ensures we abort the chain on first error, jumping to || sleep
    final_main_cmd = " && ".join(cmd_parts)
    final_cmd_str = f"(set -e; {final_main_cmd}) || {{ echo 'Job failed! Keeping container alive for 1 hour...'; sleep 3600; }}"

    container_op = {
        "id": "training-job",
        "type": "container/run",
        "args": {
            "cmd": ["/bin/bash", "-c", final_cmd_str],
            "env": envs,
            "gpu": True,
            "image": image,
            "expose": 6006,  # Simple integer expose for robustness
        },
    }

    # Return full job definition structure matching working examples
    return {
        "version": "0.1",
        "type": "container",
        "meta": {
            "trigger": "dashboard",
            "system_requirements": {
                "required_cuda": required_cuda
                or [
                    "12.4",
                    "12.8",
                    "13.0",
                    "13.2",
                ],
                "required_vram": min_vram,
                "required_gpu": gpu_count,
            },
        },
        "ops": [container_op],
    }
