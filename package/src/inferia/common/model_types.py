"""
Model Types Enum for extensible model type system.
This module defines all supported model types and their capabilities.
"""

from enum import Enum
from typing import Set, Dict, Any


class ModelType(str, Enum):
    """
    Supported model types for deployment.

    Each type represents a different workload category with specific
    serving requirements and API endpoints.
    """

    INFERENCE = "inference"
    EMBEDDING = "embedding"
    TRAINING = "training"
    BATCH = "batch"
    IMAGE_GENERATION = "image_generation"
    VIDEO_GENERATION = "video_generation"
    AUDIO_GENERATION = "audio_generation"
    MULTIMODAL = "multimodal"


class ModelCapabilities:
    """
    Defines capabilities and requirements for each model type.
    """

    CAPABILITIES: Dict[ModelType, Dict[str, Any]] = {
        ModelType.INFERENCE: {
            "api_endpoints": ["/v1/chat/completions", "/v1/completions"],
            "streaming_support": True,
            "gpu_required": True,
            "supported_backends": ["vllm", "llmd", "trt", "ollama"],
            "default_backend": "vllm",
            "description": "Large Language Models for text generation",
        },
        ModelType.EMBEDDING: {
            "api_endpoints": ["/v1/embeddings"],
            "streaming_support": False,
            "gpu_required": False,  # Can run on CPU
            "supported_backends": ["sentence-transformers", "infinity", "tei"],
            "default_backend": "infinity",
            "description": "Text embedding models for vectorization",
        },
        ModelType.TRAINING: {
            "api_endpoints": [],
            "streaming_support": False,
            "gpu_required": True,
            "supported_backends": ["pytorch", "deepspeed", "axolotl"],
            "default_backend": "pytorch",
            "description": "Fine-tuning and training workloads",
        },
        ModelType.IMAGE_GENERATION: {
            "api_endpoints": ["/v1/images/generations"],
            "streaming_support": False,
            "gpu_required": True,
            "supported_backends": ["diffusers", "sdxl", "comfyui"],
            "default_backend": "diffusers",
            "description": "Image generation models (Stable Diffusion, etc.)",
        },
        ModelType.VIDEO_GENERATION: {
            "api_endpoints": ["/v1/videos/generations"],
            "streaming_support": False,
            "gpu_required": True,
            "supported_backends": ["diffusers-video", "modelscope"],
            "default_backend": "diffusers-video",
            "description": "Video generation models",
        },
        ModelType.AUDIO_GENERATION: {
            "api_endpoints": ["/v1/audio/speech", "/v1/audio/transcriptions"],
            "streaming_support": False,
            "gpu_required": False,
            "supported_backends": ["whisper", "bark", "tts"],
            "default_backend": "whisper",
            "description": "Audio generation and transcription models",
        },
        ModelType.MULTIMODAL: {
            "api_endpoints": ["/v1/chat/completions"],
            "streaming_support": True,
            "gpu_required": True,
            "supported_backends": ["vllm", "llmd"],
            "default_backend": "vllm",
            "description": "Vision-language and multimodal models",
        },
    }

    @classmethod
    def get_capabilities(cls, model_type: ModelType) -> Dict[str, Any]:
        """Get capabilities for a model type."""
        return cls.CAPABILITIES.get(model_type, {})

    @classmethod
    def get_supported_backends(cls, model_type: ModelType) -> Set[str]:
        """Get supported backends for a model type."""
        return set(cls.CAPABILITIES.get(model_type, {}).get("supported_backends", []))

    @classmethod
    def get_default_backend(cls, model_type: ModelType) -> str:
        """Get default backend for a model type."""
        return cls.CAPABILITIES.get(model_type, {}).get("default_backend", "vllm")

    @classmethod
    def is_gpu_required(cls, model_type: ModelType) -> bool:
        """Check if GPU is required for a model type."""
        return cls.CAPABILITIES.get(model_type, {}).get("gpu_required", True)

    @classmethod
    def get_api_endpoints(cls, model_type: ModelType) -> list:
        """Get API endpoints for a model type."""
        return cls.CAPABILITIES.get(model_type, {}).get("api_endpoints", [])


# Mapping of Hugging Face pipeline tags to our model types
HF_PIPELINE_TAG_MAPPING: Dict[str, ModelType] = {
    # Text Generation / LLMs
    "text-generation": ModelType.INFERENCE,
    "text2text-generation": ModelType.INFERENCE,
    "conversational": ModelType.INFERENCE,
    # Embeddings
    "feature-extraction": ModelType.EMBEDDING,
    "sentence-similarity": ModelType.EMBEDDING,
    "sentence-embeddings": ModelType.EMBEDDING,
    # Image Generation
    "text-to-image": ModelType.IMAGE_GENERATION,
    "image-to-image": ModelType.IMAGE_GENERATION,
    "inpainting": ModelType.IMAGE_GENERATION,
    # Video Generation
    "text-to-video": ModelType.VIDEO_GENERATION,
    "image-to-video": ModelType.VIDEO_GENERATION,
    # Audio
    "text-to-speech": ModelType.AUDIO_GENERATION,
    "text-to-audio": ModelType.AUDIO_GENERATION,
    "automatic-speech-recognition": ModelType.AUDIO_GENERATION,
    "audio-to-audio": ModelType.AUDIO_GENERATION,
    # Multimodal
    "visual-question-answering": ModelType.MULTIMODAL,
    "image-text-to-text": ModelType.MULTIMODAL,
}


def infer_model_type_from_hf_tags(tags: list, pipeline_tag: str = None) -> ModelType:
    """
    Infer model type from Hugging Face model tags and pipeline_tag.

    Args:
        tags: List of HF model tags
        pipeline_tag: HF pipeline tag

    Returns:
        ModelType enum value
    """
    # Check pipeline tag first
    if pipeline_tag and pipeline_tag in HF_PIPELINE_TAG_MAPPING:
        return HF_PIPELINE_TAG_MAPPING[pipeline_tag]

    # Check tags
    for tag in tags:
        if tag in HF_PIPELINE_TAG_MAPPING:
            return HF_PIPELINE_TAG_MAPPING[tag]

    # Default to inference for unknown models
    return ModelType.INFERENCE


def get_model_type_from_string(type_str: str) -> ModelType:
    """Convert string to ModelType enum."""
    try:
        return ModelType(type_str.lower())
    except ValueError:
        return ModelType.INFERENCE  # Default fallback
