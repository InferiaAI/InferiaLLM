"""
Adapter for Anthropic Claude API.
"""

from typing import Dict, Any, Optional

from ..base import ProviderAdapter


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
