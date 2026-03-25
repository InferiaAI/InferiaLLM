"""
Provider registry: adapter factory, category sets, and helper functions.
"""

import logging
from typing import Dict

from .base import ProviderAdapter
from .external.openai import OpenAIAdapter
from .external.anthropic import AnthropicAdapter
from .external.cohere import CohereAdapter
from .engines.text import ComputeAdapter
from .engines.embedding import EmbeddingAdapter
from .engines.image import InferaDiffusionImageAdapter

logger = logging.getLogger(__name__)

# Module-level cache for adapter instances (stateless, thread-safe)
_ADAPTER_CACHE: Dict[str, ProviderAdapter] = {}


# --- Category sets (canonical names) ---

EXTERNAL_PROVIDERS = {
    "openai",
    "anthropic",
    "cohere",
    "groq",
    "gemini",
    "openrouter",
    "cerebras",
}
TEXT_INFERENCE_ENGINES = {"vllm", "ollama", "generic"}
EMBEDDING_ENGINES = {"infinity", "tei"}
IMAGE_ENGINES = {"inferia-diffusion"}
VIDEO_ENGINES = {"inferia-diffusion"}

# Backward-compat aliases
EXTERNAL_ENGINES = EXTERNAL_PROVIDERS
COMPUTE_ENGINES = TEXT_INFERENCE_ENGINES


def get_adapter(engine: str) -> ProviderAdapter:
    """
    Factory function to get the appropriate adapter for an engine.
    Adapter instances are cached for reuse since they are stateless.

    Args:
        engine: The engine type (openai, anthropic, cohere, groq, vllm, ollama, etc.)

    Returns:
        An appropriate ProviderAdapter instance
    """
    engine_lower = (engine or "").lower()

    # Check cache first
    if engine_lower in _ADAPTER_CACHE:
        return _ADAPTER_CACHE[engine_lower]

    # Create adapter instance
    adapters = {
        # External providers
        "openai": OpenAIAdapter(),
        "groq": OpenAIAdapter(),  # Groq is OpenAI-compatible
        "gemini": OpenAIAdapter(),  # Gemini OpenAI-compatible endpoint
        "openrouter": OpenAIAdapter(),  # OpenRouter is OpenAI-compatible
        "cerebras": OpenAIAdapter(),  # Cerebras is OpenAI-compatible
        "anthropic": AnthropicAdapter(),
        "cohere": CohereAdapter(),
        # Compute engines (OpenAI-compatible)
        "vllm": ComputeAdapter(),
        "ollama": ComputeAdapter(),
        "generic": ComputeAdapter(),
        # Embedding engines (OpenAI-compatible for embeddings)
        "infinity": EmbeddingAdapter(),
        "tei": EmbeddingAdapter(),
        # Image generation engines
        "inferia-diffusion": InferaDiffusionImageAdapter(),
    }

    adapter = adapters.get(engine_lower)

    if adapter is None:
        logger.warning(f"Unknown engine '{engine}', defaulting to ComputeAdapter")
        adapter = ComputeAdapter()

    # Cache and return
    _ADAPTER_CACHE[engine_lower] = adapter
    return adapter


def is_external_provider(engine: str) -> bool:
    """Check if an engine is an external provider."""
    return (engine or "").lower() in EXTERNAL_PROVIDERS


# Backward-compat alias
is_external_engine = is_external_provider
