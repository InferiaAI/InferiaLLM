"""
Adapter for OpenAI and OpenAI-compatible APIs.
"""

from typing import Dict, Any

from ..base import ProviderAdapter


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
