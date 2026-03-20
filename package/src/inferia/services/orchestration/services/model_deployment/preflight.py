"""Pre-deployment gatekeeping checks."""

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

HF_API_BASE = "https://huggingface.co/api/models"


@dataclass
class PreflightResult:
    accessible: bool = True
    needs_token: bool = False
    error: Optional[str] = None
    skipped: bool = False


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
