"""
Unified request logging for all inference request types.

Replaces the four _log_* methods (LLM, embedding, image, video)
from the original OrchestrationService with a single entry point.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from inferia.services.inference.client import api_gateway_client

logger = logging.getLogger(__name__)

# Fields to strip from payloads to avoid logging large binary blobs
_BINARY_FIELDS = {
    "image": "<base64_image_omitted>",
    "mask": "<base64_mask_omitted>",
    "video": "<base64_video_omitted>",
    "input_reference": "<base64_image_omitted>",
}

# Maximum serialized payload size stored in inference_logs (64 KB)
_MAX_PAYLOAD_BYTES = 65_536


class RequestLogger:
    """Unified inference request logger."""

    @staticmethod
    async def log(
        *,
        deployment_id: Optional[str],
        user_id: str,
        model: str,
        request_payload: Dict,
        start_time: float,
        request_type: str,
        applied_policies: List[str],
        log_payloads: bool,
        ip_address: Optional[str] = None,
        status_code: int = 200,
        error_message: Optional[str] = None,
        # LLM-specific
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        ttft_ms: Optional[float] = None,
        is_streaming: bool = False,
        # Embedding-specific
        input_count: Optional[int] = None,
        # Image/video count
        n_items: Optional[int] = None,
    ) -> None:
        end_time = time.time()
        total_duration_ms = int((end_time - start_time) * 1000)
        latency_ms = total_duration_ms
        total_tokens = prompt_tokens + completion_tokens

        # Tokens per second (LLM only)
        tokens_per_second = None
        if total_duration_ms > 0 and completion_tokens > 0:
            tokens_per_second = round(completion_tokens / (total_duration_ms / 1000), 2)

        # Sanitize payload
        final_payload = RequestLogger._sanitize_payload(
            request_payload, log_payloads, model, request_type
        )

        # Build usage dict per request type
        usage = RequestLogger._build_usage(
            request_type, prompt_tokens, completion_tokens, total_tokens,
            input_count, n_items,
        )

        # Log + track in parallel
        await asyncio.gather(
            api_gateway_client.log_inference(
                deployment_id=deployment_id,
                user_id=user_id,
                model=model,
                request_payload=final_payload,
                latency_ms=latency_ms,
                ttft_ms=ttft_ms,
                tokens_per_second=tokens_per_second,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                status_code=status_code,
                error_message=error_message,
                is_streaming=is_streaming,
                applied_policies=applied_policies,
                ip_address=ip_address,
                request_type=request_type,
                input_count=input_count or n_items,
            ),
            api_gateway_client.track_usage(user_id, model, usage),
        )

    @staticmethod
    def _sanitize_payload(
        payload: Dict, log_payloads: bool, model: str, request_type: str
    ) -> Optional[Dict]:
        if not log_payloads:
            logger.debug(f"Payload logging disabled for {request_type} request to {model}")
            return None

        if not payload:
            return None

        # Strip binary fields for image/video requests
        has_binary = any(k in payload for k in _BINARY_FIELDS)
        if has_binary:
            payload = {
                k: v for k, v in payload.items() if k not in _BINARY_FIELDS
            }
            for field_name, placeholder in _BINARY_FIELDS.items():
                if field_name in payload:
                    payload[field_name] = placeholder

        # Truncate oversized payloads to prevent table bloat
        try:
            serialized = json.dumps(payload, default=str)
            if len(serialized) > _MAX_PAYLOAD_BYTES:
                return {"_truncated": True, "_size": len(serialized)}
        except (TypeError, ValueError):
            pass

        return payload

    @staticmethod
    def _build_usage(
        request_type: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        input_count: Optional[int],
        n_items: Optional[int],
    ) -> Dict[str, Any]:
        usage: Dict[str, Any] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

        if request_type == "embedding" and input_count is not None:
            usage["input_count"] = input_count
        elif request_type.startswith("image") and n_items is not None:
            usage["image_count"] = n_items
        elif request_type.startswith("video") and n_items is not None:
            usage["video_count"] = n_items

        return usage
