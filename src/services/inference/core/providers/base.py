"""
Base class for all provider adapters.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


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

    def get_endpoint_path(self, request_type: str) -> str:
        """Returns the API path for a given request type.

        Default implementation returns standard OpenAI-compatible paths.
        Override in subclasses for provider-specific routing.

        Args:
            request_type: One of 'chat', 'embedding', 'image_generation',
                'image_edit', 'image_variations', 'video_generation',
                'video_edit', 'video_extension'.
        """
        paths = {
            "chat": self.get_chat_path(),
            "embedding": "/v1/embeddings",
            "image_generation": "/v1/images/generations",
            "image_edit": "/v1/images/edits",
            "image_variations": "/v1/images/variations",
            "video_generation": "/v1/videos/generations",
            "video_edit": "/v1/videos/edits",
            "video_extension": "/v1/videos/extensions",
        }
        return paths.get(request_type, self.get_chat_path())

    def is_external(self) -> bool:
        """Returns True if this is an external provider (requires API key)."""
        return True
