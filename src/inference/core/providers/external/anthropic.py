"""
Adapter for Anthropic Claude API.
"""

import time
from typing import Any, Dict, List, Optional

from ..base import ProviderAdapter


class AnthropicAdapter(ProviderAdapter):
    """
    Adapter for Anthropic Claude API.
    Transforms between OpenAI format and Anthropic's /v1/messages format.
    """

    stream_is_openai_format = False

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

    def _chunk(
        self,
        state: Dict[str, Any],
        delta: Dict[str, Any],
        finish_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """One OpenAI chat.completion.chunk, built from what the stream has seen."""
        return {
            "id": state.get("id", ""),
            "object": "chat.completion.chunk",
            "created": state.setdefault("created", int(time.time())),
            "model": state.get("model", ""),
            "choices": [
                {"index": 0, "delta": delta, "finish_reason": finish_reason}
            ],
        }

    def transform_stream_event(
        self, event: Dict[str, Any], state: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Translate one Anthropic SSE event into OpenAI chunks.

        Anthropic opens with a message envelope, streams text as deltas on a
        content block, and closes with a stop reason. OpenAI carries all of
        that on one chunk shape, so several Anthropic event types have no
        OpenAI equivalent and are dropped.
        """
        etype = event.get("type")

        if etype == "message_start":
            message = event.get("message") or {}
            # Held for the rest of the stream: later events do not repeat them.
            state["id"] = message.get("id", "")
            state["model"] = message.get("model", "")
            return [self._chunk(state, {"role": "assistant"})]

        if etype == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if text:
                    return [self._chunk(state, {"content": text})]
            return []

        if etype == "message_delta":
            reason = (event.get("delta") or {}).get("stop_reason")
            return [self._chunk(state, {}, self._map_stop_reason(reason))]

        # ping, content_block_start, content_block_stop, message_stop.
        return []

    def finalize_stream(self, state: Dict[str, Any]) -> List[Any]:
        """Anthropic never sends `[DONE]`, and an OpenAI client waits for it."""
        return ["[DONE]"]

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
