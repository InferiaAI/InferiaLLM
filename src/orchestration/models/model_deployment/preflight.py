"""Pre-deployment gatekeeping checks."""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

HF_API_BASE = "https://huggingface.co/api/models"
HF_RESOLVE_BASE = "https://huggingface.co"

# Field names HF model configs use for the native context window. Multimodal
# configs (e.g. Gemma 3) nest the language model under "text_config".
_CTX_FIELDS = (
    "max_position_embeddings",
    "max_sequence_length",
    "seq_length",
    "n_positions",
    "max_seq_len",
)


def _native_context_from_config(config: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract a model's native context window from an HF config dict.

    Checks the top level and a nested ``text_config`` (multimodal models).
    Returns the int, or None when no recognized field is present.
    """
    if not isinstance(config, dict):
        return None
    sources = [config]
    text_cfg = config.get("text_config")
    if isinstance(text_cfg, dict):
        sources.append(text_cfg)
    for src in sources:
        for field_name in _CTX_FIELDS:
            val = src.get(field_name)
            if isinstance(val, int) and val > 0:
                return val
    return None


async def fetch_native_max_len(
    model_id: str, hf_token: Optional[str] = None
) -> Optional[int]:
    """Best-effort fetch of a model's native context window from its config.json.

    Used to clamp a deploy's ``max_model_len`` so it never exceeds the model's
    native context — vLLM HARD-ERRORS at startup otherwise (and the container
    crashes during model load). Returns None on any failure (gated / missing /
    parse error); callers then let the engine derive the context itself.
    """
    url = f"{HF_RESOLVE_BASE}/{model_id}/resolve/main/config.json"
    headers = {}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return _native_context_from_config(resp.json())
    except Exception as e:
        logger.warning("Failed to fetch native context for %s: %s", model_id, e)
    return None

# Engines that require config.json (transformers-based)
ENGINES_REQUIRING_CONFIG_JSON = {"vllm", "sglang", "tei", "infinity"}

# Engines that work with GGUF files
ENGINES_SUPPORTING_GGUF = {"ollama", "localai", "llama.cpp"}

# Engines that use their own model registry (not HuggingFace)
# These use model_name:tag format and should skip HF checks
ENGINES_WITH_OWN_REGISTRY = {"ollama", "localai"}

OLLAMA_REGISTRY_BASE = "https://ollama.com"

# Engine → expected pipeline_tag mapping
ENGINE_PIPELINE_MAP = {
    "vllm": {"text-generation"},
    "sglang": {"text-generation"},
    "tei": {"feature-extraction", "sentence-similarity"},
    "infinity": {"feature-extraction", "sentence-similarity"},
    "localai": {
        "text-generation",
        "text-to-image",
        "text-to-video",
        "feature-extraction",
    },
    "inferia-diffusion": {"text-to-image", "text-to-video"},
    "ollama": {"text-generation"},
}

# Approximate VRAM per parameter in bytes (BF16/FP16 = 2 bytes/param)
BYTES_PER_PARAM_FP16 = 2.0
# KV cache and overhead multiplier (vLLM needs ~20-30% overhead)
VRAM_OVERHEAD_MULTIPLIER = 1.25
# Common GPU VRAM sizes in GB
GPU_VRAM_GB = 24  # Default assumption (A10G, RTX 4090, L4)


@dataclass
class PreflightResult:
    accessible: bool = True
    needs_token: bool = False
    error: Optional[str] = None
    skipped: bool = False


@dataclass
class FormatCheckResult:
    compatible: bool = True
    error: Optional[str] = None
    skipped: bool = False
    has_config_json: bool = False
    has_gguf: bool = False
    files: List[str] = field(default_factory=list)


async def check_model_accessibility(
    model_id: str,
    hf_token: Optional[str] = None,
) -> PreflightResult:
    """
    Check if a HuggingFace model is accessible.
    Fails open (accessible=True, skipped=True) if HF API is unreachable.
    """
    url = f"{HF_API_BASE}/{model_id}"
    headers = {}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.head(url, headers=headers)

            if resp.status_code == 200:
                return PreflightResult(accessible=True)

            if resp.status_code in (401, 403):
                if hf_token:
                    return PreflightResult(
                        accessible=False,
                        needs_token=False,
                        error="HF token provided but model access denied. Check token permissions or model license agreement.",
                    )
                return PreflightResult(
                    accessible=False,
                    needs_token=True,
                    error="Model is gated. Provide a HuggingFace token with access.",
                )

            if resp.status_code == 404:
                return PreflightResult(
                    accessible=False,
                    error=f"Model '{model_id}' not found on HuggingFace.",
                )

            logger.warning(
                "Unexpected HF API status %s for model %s", resp.status_code, model_id
            )
            return PreflightResult(accessible=True, skipped=True)

    except Exception as e:
        logger.warning("HF accessibility check failed for %s: %s", model_id, e)
        return PreflightResult(accessible=True, skipped=True)


async def check_model_format(
    model_id: str,
    engine: str,
    hf_token: Optional[str] = None,
) -> FormatCheckResult:
    """
    Check if a HuggingFace model's format is compatible with the target engine.
    vLLM/TEI/Infinity require config.json; Ollama/LocalAI can use GGUF files.
    Fails open if HF API is unreachable.
    """
    engine_lower = engine.lower() if engine else ""

    # If engine doesn't require config.json, skip
    if engine_lower not in ENGINES_REQUIRING_CONFIG_JSON:
        return FormatCheckResult(compatible=True, skipped=True)

    url = f"{HF_API_BASE}/{model_id}"
    headers = {}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)

            if resp.status_code != 200:
                return FormatCheckResult(compatible=True, skipped=True)

            data = resp.json()
            siblings = data.get("siblings", [])
            filenames = [s.get("rfilename", "") for s in siblings]

            has_config = "config.json" in filenames
            has_gguf = any(f.endswith(".gguf") for f in filenames)

            if has_config:
                return FormatCheckResult(
                    compatible=True,
                    has_config_json=True,
                    has_gguf=has_gguf,
                    files=filenames,
                )

            # No config.json — incompatible with this engine
            if has_gguf:
                suggestion = (
                    f"Model '{model_id}' only contains GGUF files and is not compatible "
                    f"with {engine}. Use Ollama or LocalAI engine instead, or choose "
                    f"the original (non-quantized) model."
                )
            else:
                suggestion = (
                    f"Model '{model_id}' does not contain a config.json file "
                    f"required by {engine}."
                )

            return FormatCheckResult(
                compatible=False,
                error=suggestion,
                has_config_json=False,
                has_gguf=has_gguf,
                files=filenames,
            )

    except Exception as e:
        logger.warning("HF format check failed for %s: %s", model_id, e)
        return FormatCheckResult(compatible=True, skipped=True)


# ── Ollama / own-registry model check ────────────────────────────────


@dataclass
class OllamaCheckResult:
    accessible: bool = True
    error: Optional[str] = None
    skipped: bool = False


async def check_ollama_model_exists(model_id: str) -> OllamaCheckResult:
    """
    Check if a model exists in the Ollama registry.

    Ollama model formats:
    - Standard: "llama3", "llama3:8b"
    - Namespaced: "mannix/llama3.1-8b-abliterated"

    URL patterns:
    - Standard: https://ollama.com/library/{name} or https://ollama.com/library/{name}:{tag}
    - Namespaced: https://ollama.com/{namespace}/{name}
    """
    if not model_id:
        return OllamaCheckResult(accessible=True, skipped=True)

    # Determine URL based on whether model_id has a namespace (contains /)
    base_name = model_id.split(":")[0]  # strip tag
    if "/" in base_name:
        # Namespaced: mannix/llama3.1-8b → https://ollama.com/mannix/llama3.1-8b
        url = f"{OLLAMA_REGISTRY_BASE}/{base_name}"
    else:
        # Standard: llama3:8b → https://ollama.com/library/llama3:8b
        url = f"{OLLAMA_REGISTRY_BASE}/library/{model_id}"

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.head(url)

            if resp.status_code == 200:
                return OllamaCheckResult(accessible=True)

            if resp.status_code == 404:
                return OllamaCheckResult(
                    accessible=False,
                    error=f"Model '{model_id}' not found in Ollama registry. Check the model name at ollama.com/library.",
                )

            return OllamaCheckResult(accessible=True, skipped=True)

    except Exception as e:
        logger.warning("Ollama registry check failed for %s: %s", model_id, e)
        return OllamaCheckResult(accessible=True, skipped=True)


# ── Shared HF metadata fetcher ──────────────────────────────────────


async def fetch_hf_model_info(
    model_id: str, hf_token: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Fetch full model metadata from HF API. Returns None on failure."""
    url = f"{HF_API_BASE}/{model_id}"
    headers = {}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.warning("Failed to fetch HF model info for %s: %s", model_id, e)
    return None


# ── Check 3: VRAM estimation ────────────────────────────────────────


@dataclass
class VRAMCheckResult:
    ok: bool = True
    error: Optional[str] = None
    skipped: bool = False
    estimated_vram_gb: float = 0.0
    available_vram_gb: float = 0.0
    param_count: int = 0


def estimate_vram_gb(param_count: int) -> float:
    """Estimate minimum VRAM in GB for FP16 inference with overhead."""
    if param_count <= 0:
        return 0.0
    raw_gb = (param_count * BYTES_PER_PARAM_FP16) / (1024**3)
    return round(raw_gb * VRAM_OVERHEAD_MULTIPLIER, 1)


def check_vram_fit(
    hf_info: Optional[Dict[str, Any]],
    gpu_per_replica: int = 1,
    gpu_vram_gb: float = GPU_VRAM_GB,
) -> VRAMCheckResult:
    """Check if model fits in available VRAM based on parameter count."""
    if not hf_info:
        return VRAMCheckResult(ok=True, skipped=True)

    # Skip VRAM check for CPU-only deployments (e.g., embedding models with 0 GPUs)
    if gpu_per_replica <= 0:
        return VRAMCheckResult(ok=True, skipped=True)

    safetensors = hf_info.get("safetensors", {})
    params = safetensors.get("parameters", {})

    # Get total param count — prefer BF16/F16, fall back to any dtype
    param_count = params.get("BF16") or params.get("F16") or 0
    if not param_count:
        # Sum all dtype counts (some models list I64, F32, etc.)
        param_count = sum(v for v in params.values() if isinstance(v, (int, float)))

    if param_count <= 0:
        return VRAMCheckResult(ok=True, skipped=True)

    estimated = estimate_vram_gb(param_count)
    available = gpu_per_replica * gpu_vram_gb

    if estimated > available:
        return VRAMCheckResult(
            ok=False,
            estimated_vram_gb=estimated,
            available_vram_gb=available,
            param_count=param_count,
            error=(
                f"Model requires ~{estimated} GB VRAM ({param_count / 1e9:.1f}B parameters) "
                f"but only {available} GB available ({gpu_per_replica}x {gpu_vram_gb} GB GPU). "
                f"Use more GPUs, a smaller model, or a quantized variant."
            ),
        )

    return VRAMCheckResult(
        ok=True,
        estimated_vram_gb=estimated,
        available_vram_gb=available,
        param_count=param_count,
    )


# ── Check 4: Pipeline tag vs engine compatibility ────────────────────


@dataclass
class PipelineCheckResult:
    compatible: bool = True
    error: Optional[str] = None
    skipped: bool = False
    pipeline_tag: Optional[str] = None


def check_pipeline_compatibility(
    hf_info: Optional[Dict[str, Any]],
    engine: str,
    model_type: str = "inference",
) -> PipelineCheckResult:
    """Check if the model's pipeline_tag matches the target engine."""
    if not hf_info:
        return PipelineCheckResult(compatible=True, skipped=True)

    engine_lower = engine.lower() if engine else ""
    expected_tags = ENGINE_PIPELINE_MAP.get(engine_lower)
    if not expected_tags:
        return PipelineCheckResult(compatible=True, skipped=True)

    pipeline_tag = hf_info.get("pipeline_tag")
    if not pipeline_tag:
        return PipelineCheckResult(compatible=True, skipped=True)

    if pipeline_tag in expected_tags:
        return PipelineCheckResult(compatible=True, pipeline_tag=pipeline_tag)

    # Mismatch
    friendly = {
        "text-generation": "text generation (chat/completion)",
        "feature-extraction": "embeddings",
        "text-to-image": "image generation",
        "text-to-video": "video generation",
        "image-to-video": "video generation",
        "sentence-similarity": "embeddings",
    }
    model_purpose = friendly.get(pipeline_tag, pipeline_tag)
    engine_purpose = ", ".join(friendly.get(t, t) for t in expected_tags)

    return PipelineCheckResult(
        compatible=False,
        pipeline_tag=pipeline_tag,
        error=(
            f"Model is for {model_purpose} but {engine} expects {engine_purpose}. "
            f"Choose a different model or engine."
        ),
    )


# ── Check 5: Docker image existence ─────────────────────────────────


@dataclass
class ImageCheckResult:
    exists: bool = True
    error: Optional[str] = None
    skipped: bool = False


async def check_docker_image_exists(image: Optional[str]) -> ImageCheckResult:
    """Check if a Docker image tag exists on the registry via HEAD to manifest."""
    if not image:
        return ImageCheckResult(exists=True, skipped=True)

    # Parse image: registry/repo:tag
    # Handle docker.io special case
    parts = image.split("/", 2)
    if len(parts) < 2:
        return ImageCheckResult(exists=True, skipped=True)

    # Split tag
    repo_and_tag = image
    if ":" in parts[-1]:
        tag = parts[-1].split(":")[-1]
        repo_path = image.rsplit(":", 1)[0]
    else:
        tag = "latest"
        repo_path = image

    # Only check docker.io images (most common for vLLM, LocalAI, etc.)
    if not repo_path.startswith("docker.io/"):
        return ImageCheckResult(exists=True, skipped=True)

    # docker.io/library/X or docker.io/org/repo → registry-1.docker.io/v2/org/repo/manifests/tag
    path = repo_path.replace("docker.io/", "", 1)
    url = f"https://registry-1.docker.io/v2/{path}/manifests/{tag}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Get auth token first (Docker Hub requires this)
            token_resp = await client.get(
                f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{path}:pull"
            )
            if token_resp.status_code != 200:
                return ImageCheckResult(exists=True, skipped=True)

            token = token_resp.json().get("token", "")
            resp = await client.head(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.docker.distribution.manifest.v2+json",
                },
            )

            if resp.status_code == 200:
                return ImageCheckResult(exists=True)
            if resp.status_code == 404:
                return ImageCheckResult(
                    exists=False,
                    error=f"Docker image '{image}' not found. Verify the image tag exists.",
                )
            return ImageCheckResult(exists=True, skipped=True)

    except Exception as e:
        logger.warning("Docker image check failed for %s: %s", image, e)
        return ImageCheckResult(exists=True, skipped=True)


# ── Check 6: Duplicate deployment ────────────────────────────────────


@dataclass
class DuplicateCheckResult:
    ok: bool = True
    error: Optional[str] = None
    skipped: bool = False
    existing_deployment_id: Optional[str] = None


async def check_duplicate_deployment(
    model_id: str,
    pool_id: str,
    db_pool,
) -> DuplicateCheckResult:
    """Check if the same model is already running on the same pool."""
    if not db_pool or not pool_id or pool_id == "00000000-0000-0000-0000-000000000000":
        return DuplicateCheckResult(ok=True, skipped=True)

    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT deployment_id FROM model_deployments
                WHERE inference_model = $1
                  AND pool_id = $2::uuid
                  AND UPPER(state) IN ('RUNNING', 'READY', 'PENDING', 'PROVISIONING', 'DEPLOYING')
                LIMIT 1
                """,
                model_id,
                pool_id,
            )
            if row:
                return DuplicateCheckResult(
                    ok=False,
                    existing_deployment_id=str(row["deployment_id"]),
                    error=(
                        f"Model '{model_id}' already has an active deployment on this pool "
                        f"(ID: {str(row['deployment_id'])[:8]}...). Terminate it first or use a different pool."
                    ),
                )
            return DuplicateCheckResult(ok=True)

    except Exception as e:
        logger.warning("Duplicate deployment check failed: %s", e)
        return DuplicateCheckResult(ok=True, skipped=True)


# ── Check 7: Max model length vs context window ─────────────────────


@dataclass
class ContextLengthCheckResult:
    ok: bool = True
    error: Optional[str] = None
    skipped: bool = False


def check_context_length(
    hf_info: Optional[Dict[str, Any]],
    max_model_len: Optional[int] = None,
) -> ContextLengthCheckResult:
    """Check if requested max_model_len exceeds the model's native context window."""
    if not hf_info or not max_model_len:
        return ContextLengthCheckResult(ok=True, skipped=True)

    config = hf_info.get("config", {}) or {}
    native_ctx = _native_context_from_config(config)

    if not native_ctx:
        return ContextLengthCheckResult(ok=True, skipped=True)

    if max_model_len > native_ctx:
        return ContextLengthCheckResult(
            ok=False,
            error=(
                f"Requested max_model_len ({max_model_len}) exceeds model's native "
                f"context window ({native_ctx}). vLLM will reject this at startup. "
                f"Use {native_ctx} or lower."
            ),
        )

    return ContextLengthCheckResult(ok=True)
