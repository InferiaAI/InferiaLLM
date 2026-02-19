"""
Provider Adapters for External API Compatibility

This module provides adapters for different LLM providers to normalize
their APIs to a common OpenAI-compatible format.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)

# Module-level cache for adapter instances (stateless, thread-safe)
_ADAPTER_CACHE: Dict[str, "ProviderAdapter"] = {}


# Engine categories for routing
EXTERNAL_ENGINES = {"openai", "anthropic", "cohere", "groq"}
COMPUTE_ENGINES = {"vllm", "ollama", "generic"}


class ProviderAdapter(ABC):
    """Base class for provider adapters."""

    @abstractmethod
    def get_chat_path(self) -> str:
        """Returns the API path for chat completions."""
        pass

    @abstractmethod
    def get_headers(self, api_key: str) -> Dict[str, str]:
        """Returns provider-specific headers."""
        pass

    @abstractmethod
    def transform_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Transforms OpenAI-format request to provider format."""
        pass

    @abstractmethod
    def transform_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Transforms provider response to OpenAI format."""
        pass

    def is_external(self) -> bool:
        """Returns True if this is an external provider (requires API key)."""
        return True


class OpenAIAdapter(ProviderAdapter):
    """
    Adapter for OpenAI and OpenAI-compatible APIs.
    Used for: OpenAI, Groq, vLLM, and other OpenAI-compatible endpoints.
    """

    def get_chat_path(self) -> str:
        return "/v1/chat/completions"

    def get_headers(self, api_key: str) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def transform_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # OpenAI format is our native format, no transformation needed
        return payload

    def transform_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        # Already in OpenAI format
        return response


class AnthropicAdapter(ProviderAdapter):
    """
    Adapter for Anthropic Claude API.
    Transforms between OpenAI format and Anthropic's /v1/messages format.
    """

    def get_chat_path(self) -> str:
        return "/v1/messages"

    def get_headers(self, api_key: str) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if api_key:
            headers["x-api-key"] = api_key
        return headers

    def transform_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Convert OpenAI format to Anthropic format."""
        messages = payload.get("messages", [])

        # Extract system message if present
        system_content = None
        claude_messages = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                system_content = content
            elif role == "user":
                claude_messages.append({"role": "user", "content": content})
            elif role == "assistant":
                claude_messages.append({"role": "assistant", "content": content})

        anthropic_payload = {
            "model": payload.get("model"),
            "messages": claude_messages,
            "max_tokens": payload.get("max_tokens", 4096),
        }

        if system_content:
            anthropic_payload["system"] = system_content

        if payload.get("stream"):
            anthropic_payload["stream"] = True

        if payload.get("temperature") is not None:
            anthropic_payload["temperature"] = payload["temperature"]

        if payload.get("top_p") is not None:
            anthropic_payload["top_p"] = payload["top_p"]

        return anthropic_payload

    def transform_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Anthropic response to OpenAI format."""
        # Extract content from Anthropic response
        content_blocks = response.get("content", [])
        text_content = ""
        for block in content_blocks:
            if block.get("type") == "text":
                text_content += block.get("text", "")

        # Build OpenAI-compatible response
        return {
            "id": response.get("id", ""),
            "object": "chat.completion",
            "created": 0,  # Anthropic doesn't provide this
            "model": response.get("model", ""),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": text_content,
                    },
                    "finish_reason": self._map_stop_reason(response.get("stop_reason")),
                }
            ],
            "usage": {
                "prompt_tokens": response.get("usage", {}).get("input_tokens", 0),
                "completion_tokens": response.get("usage", {}).get("output_tokens", 0),
                "total_tokens": (
                    response.get("usage", {}).get("input_tokens", 0)
                    + response.get("usage", {}).get("output_tokens", 0)
                ),
            },
        }

    def _map_stop_reason(self, anthropic_reason: Optional[str]) -> str:
        """Map Anthropic stop reasons to OpenAI finish_reason."""
        if anthropic_reason is None:
            return "stop"
        mapping = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
        }
        return mapping.get(anthropic_reason, "stop")


class CohereAdapter(ProviderAdapter):
    """
    Adapter for Cohere API.
    Transforms between OpenAI format and Cohere's /v1/chat format.
    """

    def get_chat_path(self) -> str:
        return "/v1/chat"

    def get_headers(self, api_key: str) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def transform_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Convert OpenAI format to Cohere format."""
        messages = payload.get("messages", [])

        # Extract chat history and current message
        chat_history = []
        message = ""
        preamble = None

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                preamble = content
            elif role == "user":
                if message:  # Previous user message goes to history
                    chat_history.append({"role": "USER", "message": message})
                message = content
            elif role == "assistant":
                chat_history.append({"role": "CHATBOT", "message": content})

        cohere_payload = {
            "model": payload.get("model"),
            "message": message,
        }

        if chat_history:
            cohere_payload["chat_history"] = chat_history

        if preamble:
            cohere_payload["preamble"] = preamble

        if payload.get("stream"):
            cohere_payload["stream"] = True

        if payload.get("temperature") is not None:
            cohere_payload["temperature"] = payload["temperature"]

        return cohere_payload

    def transform_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Cohere response to OpenAI format."""
        return {
            "id": response.get("generation_id", ""),
            "object": "chat.completion",
            "created": 0,
            "model": response.get("model", ""),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response.get("text", ""),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": response.get("meta", {})
                .get("tokens", {})
                .get("input_tokens", 0),
                "completion_tokens": response.get("meta", {})
                .get("tokens", {})
                .get("output_tokens", 0),
                "total_tokens": (
                    response.get("meta", {}).get("tokens", {}).get("input_tokens", 0)
                    + response.get("meta", {}).get("tokens", {}).get("output_tokens", 0)
                ),
            },
        }


class ComputeAdapter(OpenAIAdapter):
    """
    Adapter for compute-deployed models (vLLM, Ollama).
    These are OpenAI-compatible, so we inherit from OpenAIAdapter.
    """

    def is_external(self) -> bool:
        return False


class EmbeddingAdapter(OpenAIAdapter):
    """
    Adapter for embedding models (Infinity, TEI).
    These are OpenAI-compatible for embeddings, so we inherit from OpenAIAdapter.
    """

    def is_external(self) -> bool:
        return False


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
        "anthropic": AnthropicAdapter(),
        "cohere": CohereAdapter(),
        # Compute engines (OpenAI-compatible)
        "vllm": ComputeAdapter(),
        "ollama": ComputeAdapter(),
        "generic": ComputeAdapter(),
        # Embedding engines (OpenAI-compatible for embeddings)
        "infinity": EmbeddingAdapter(),
        "tei": EmbeddingAdapter(),
    }

    adapter = adapters.get(engine_lower)

    if adapter is None:
        logger.warning(f"Unknown engine '{engine}', defaulting to ComputeAdapter")
        adapter = ComputeAdapter()

    # Cache and return
    _ADAPTER_CACHE[engine_lower] = adapter
    return adapter


def is_external_engine(engine: str) -> bool:
    """Check if an engine is an external provider."""
    return (engine or "").lower() in EXTERNAL_ENGINES
