"""
Provider Adapters for External API Compatibility

This package provides adapters for different LLM providers to normalize
their APIs to a common OpenAI-compatible format.
"""

# Base
from .base import ProviderAdapter

# External providers
from .external.openai import OpenAIAdapter
from .external.anthropic import AnthropicAdapter
from .external.cohere import CohereAdapter

# Engine adapters
from .engines.text import ComputeAdapter
from .engines.embedding import EmbeddingAdapter
from .engines.image import InferaDiffusionImageAdapter
from .engines.video import InferaDiffusionVideoAdapter

# Registry: factory, categories, helpers
from .registry import (
    get_adapter,
    is_external_provider,
    is_external_engine,
    resolve_upstream,
    EXTERNAL_PROVIDERS,
    TEXT_INFERENCE_ENGINES,
    EMBEDDING_ENGINES,
    IMAGE_ENGINES,
    VIDEO_ENGINES,
    EXTERNAL_ENGINES,
    COMPUTE_ENGINES,
)

# Backward-compat alias
InferaDiffusionAdapter = InferaDiffusionImageAdapter

__all__ = [
    # Base
    "ProviderAdapter",
    # External
    "OpenAIAdapter",
    "AnthropicAdapter",
    "CohereAdapter",
    # Engines
    "ComputeAdapter",
    "EmbeddingAdapter",
    "InferaDiffusionImageAdapter",
    "InferaDiffusionVideoAdapter",
    "InferaDiffusionAdapter",  # backward-compat alias
    # Registry
    "get_adapter",
    "is_external_provider",
    "is_external_engine",
    "resolve_upstream",
    # Category sets
    "EXTERNAL_PROVIDERS",
    "TEXT_INFERENCE_ENGINES",
    "EMBEDDING_ENGINES",
    "IMAGE_ENGINES",
    "VIDEO_ENGINES",
    "EXTERNAL_ENGINES",
    "COMPUTE_ENGINES",
]
