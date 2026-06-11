"""
Orchestration facade — delegates to handler modules.

This module preserves the OrchestrationService interface so that
app.py and existing consumers require zero changes.
"""

import logging

from inference.client import api_gateway_client
from .handlers import CompletionHandler, EmbeddingHandler, ImageHandler, VideoHandler

# Re-export dependencies that tests patch via this module's namespace.
from .service import GatewayService  # noqa: F401
from .providers import get_adapter  # noqa: F401
from .rate_limiter import rate_limiter  # noqa: F401
from inference.config import settings  # noqa: F401

logger = logging.getLogger(__name__)


class OrchestrationService:
    """
    Orchestrates the lifecycle of an inference request:
    Auth -> Context -> RateLimit -> Quota -> Inference -> Logging

    Each handler is implemented in its own module under core/handlers/.
    """

    @staticmethod
    async def list_models(api_key: str, sandbox: bool = False):
        return await api_gateway_client.list_models(f"Bearer {api_key}")

    # --- Chat Completion ---
    handle_completion = CompletionHandler.handle

    # --- Embeddings ---
    handle_embeddings = EmbeddingHandler.handle

    # --- Image ---
    handle_image_generation = ImageHandler.handle_generation
    handle_image_edit = ImageHandler.handle_edit
    handle_image_variations = ImageHandler.handle_variations

    # --- Video ---
    handle_video_generation = VideoHandler.handle_generation
    handle_video_edit = VideoHandler.handle_edit
    handle_video_extension = VideoHandler.handle_extension
    handle_video_status = VideoHandler.handle_status
