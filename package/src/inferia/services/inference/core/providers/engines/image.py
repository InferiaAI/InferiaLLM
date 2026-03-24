"""
Adapter for InferaDiffusion image generation.
"""

import logging
from typing import Dict, Any

from ..base import ProviderAdapter

logger = logging.getLogger(__name__)


class _DiffusionBaseMixin:
    """
    Shared logic for InferaDiffusion image and video adapters.
    Contains: get_chat_path, get_headers, transform_request, is_external,
    and get_endpoint_path with all image + video paths.
    """

    def get_chat_path(self) -> str:
        return "/v1/images/generations"

    def get_image_generation_path(self) -> str:
        return "/v1/images/generations"

    def get_image_edit_path(self) -> str:
        return "/v1/images/edits"

    def get_image_variations_path(self) -> str:
        return "/v1/images/variations"

    def get_video_generation_path(self) -> str:
        return "/generate/v1/videos/generations"

    def get_video_edit_path(self) -> str:
        return "/generate/v1/videos/edits"

    def get_video_extension_path(self) -> str:
        return "/generate/v1/videos/extensions"

    def get_headers(self, api_key: str) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def transform_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform to InferaDiffusion/OpenAI image/video generation format.
        Supports:
          - prompt (required for text-to-image/video)
          - model (optional, backend model name)
          - n (number of outputs, default 1)
          - size (e.g. "512x512", "1280x720")
          - response_format (url or b64_json)
          - step (diffusion steps)
          - seed, mode, scheduler, strength
          - seconds (video duration, 4-20)
          - input_reference (image for image-to-video)
          - image (for image-to-image)
          - mask (for inpainting)
        """
        transformed = {}

        if "prompt" in payload:
            transformed["prompt"] = payload["prompt"]

        for field in ("model", "n", "size", "response_format", "quality", "style"):
            if field in payload:
                transformed[field] = payload[field]

        for field in ("step", "seed", "mode", "scheduler", "strength"):
            if field in payload:
                transformed[field] = payload[field]

        for field in ("seconds", "input_reference", "image", "mask"):
            if field in payload:
                transformed[field] = payload[field]

        return transformed

    def get_endpoint_path(self, request_type: str) -> str:
        paths = {
            "image_generation": self.get_image_generation_path(),
            "image_edit": self.get_image_edit_path(),
            "image_variations": self.get_image_variations_path(),
            "video_generation": self.get_video_generation_path(),
            "video_edit": self.get_video_edit_path(),
            "video_extension": self.get_video_extension_path(),
        }
        return paths.get(request_type, self.get_chat_path())

    def is_external(self) -> bool:
        return False


class InferaDiffusionImageAdapter(_DiffusionBaseMixin, ProviderAdapter):
    """
    Adapter for InferaDiffusion image generation.
    Supports:
    - Text-to-image (POST /v1/images/generations)
    - Image-to-image (POST /v1/images/edits)
    - Image variations (POST /v1/images/variations)
    """

    def transform_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Returns OpenAI-compatible image/video response format."""
        logger.debug(f"InferaDiffusion raw response: {response}")

        # Handle video status response - convert to OpenAI format
        if response.get("status") == "completed":
            video_data = response.get("video") or response.get("output")
            if video_data:
                return {
                    "object": "video",
                    "data": [
                        {
                            "id": response.get("id", ""),
                            "url": video_data
                            if video_data.startswith("data:")
                            else f"data:video/mp4;base64,{video_data}",
                            "video_url": video_data
                            if video_data.startswith("data:")
                            else f"data:video/mp4;base64,{video_data}",
                        }
                    ],
                }

        # Pass through other responses as-is (already in OpenAI format)
        return response
