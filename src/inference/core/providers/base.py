"""
Base class for all provider adapters.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class ProviderAdapter(ABC):
    """Base class for provider adapters."""

    #: Whether this provider already streams in OpenAI's format. When it does
    #: the stream passes through untouched, including SSE comments, which are
    #: keepalives an idle connection depends on.
    stream_is_openai_format: bool = True

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

    def transform_stream_event(
        self, event: Dict[str, Any], state: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Translate one SSE event from the provider into OpenAI chunks.

        The streaming counterpart to `transform_response`. That one takes a
        whole response body, which a stream never has, so each event is
        translated as it arrives.

        Returns a list because the mapping is not one to one: a provider may
        send events that carry no content and should be dropped, or one event
        whose contents become several chunks.

        `state` is a fresh dict per stream, for translators that need to carry
        something between events. Adapters are cached module-level and shared
        by every concurrent request, so this is the only place such state can
        live without two streams corrupting each other.

        The default passes events through unchanged, which is correct for every
        provider already speaking OpenAI's format.
        """
        return [event]

    def finalize_stream(self, state: Dict[str, Any]) -> List[Any]:
        """Chunks to emit once the provider's stream closes.

        For providers that end a stream differently from OpenAI. A plain string
        is emitted after `data: ` verbatim, which is how `[DONE]` is sent, since
        it is a sentinel rather than JSON.

        The default is nothing, because an OpenAI-format stream already carries
        its own terminator.
        """
        return []

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
