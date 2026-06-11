"""
Adapter for InferaDiffusion video generation.
"""

import logging
from typing import Dict, Any

from ..base import ProviderAdapter
from .image import _DiffusionBaseMixin

logger = logging.getLogger(__name__)


class InferaDiffusionVideoAdapter(_DiffusionBaseMixin, ProviderAdapter):
    """
    Adapter for InferaDiffusion video generation.
    Supports:
    - Text-to-video (POST /v1/videos/generations)
    - Image-to-video (POST /v1/videos/generations with input_reference)
    - Video edits (POST /v1/videos/edits)
    - Video extensions (POST /v1/videos/extensions)
    """

    def transform_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Returns OpenAI-compatible video response format."""
        logger.debug(f"InferaDiffusionVideo raw response: {response}")

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

        # Pass through other responses as-is
        return response
