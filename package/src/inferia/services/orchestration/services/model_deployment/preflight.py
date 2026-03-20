"""Pre-deployment gatekeeping checks."""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

HF_API_BASE = "https://huggingface.co/api/models"

# Engines that require config.json (transformers-based)
ENGINES_REQUIRING_CONFIG_JSON = {"vllm", "tei", "infinity"}

# Engines that work with GGUF files
ENGINES_SUPPORTING_GGUF = {"ollama", "localai", "llama.cpp"}


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

            logger.warning("Unexpected HF API status %s for model %s", resp.status_code, model_id)
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
