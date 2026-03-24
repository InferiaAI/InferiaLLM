"""
Adapter for Cohere API.
"""

from typing import Dict, Any

from ..base import ProviderAdapter


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
