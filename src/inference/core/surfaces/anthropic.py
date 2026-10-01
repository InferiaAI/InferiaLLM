"""The Anthropic Messages API as a client-facing surface.

Translates `/v1/messages` requests into the OpenAI shape the gateway uses
internally, and translates the answer back. Everything between the two is the
existing path, so API key resolution, quotas, rate limiting, routing and
logging are shared with `/v1/chat/completions` rather than reimplemented.

This runs the opposite way to `AnthropicAdapter`, which translates for calls
*to* Anthropic. The field mappings are the same in both directions; the
direction is not.
"""

import json
import uuid
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException

# Anthropic names a reason for stopping; OpenAI names a reason for finishing.
_FINISH_TO_STOP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "end_turn",
}


def _flatten_content(content: Any) -> str:
    """Anthropic content is a string or a list of typed blocks.

    Only text blocks can cross into the OpenAI shape used internally. Anything
    else is rejected rather than dropped: a request that quietly loses its
    image still answers, confidently, about nothing.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    unsupported = sorted({
        block.get("type", "unknown")
        for block in content
        if isinstance(block, dict) and block.get("type") != "text"
    })
    if unsupported:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported content block type(s): "
                f"{', '.join(unsupported)}. Only text is supported."
            ),
        )

    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict)
    )


def request_to_openai(body: Dict[str, Any]) -> Dict[str, Any]:
    """An Anthropic Messages request as an OpenAI chat completion request."""
    messages: List[Dict[str, Any]] = []

    # Anthropic carries the system prompt beside the messages, not inside them.
    system = body.get("system")
    if system:
        messages.append({"role": "system", "content": _flatten_content(system)})

    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        messages.append({
            "role": message.get("role", "user"),
            "content": _flatten_content(message.get("content")),
        })

    openai_body: Dict[str, Any] = {
        "model": body.get("model"),
        "messages": messages,
    }

    # max_tokens is required by Anthropic and optional for OpenAI, so it only
    # travels when the client sent it.
    for ours, theirs in (
        ("max_tokens", "max_tokens"),
        ("temperature", "temperature"),
        ("top_p", "top_p"),
        ("stream", "stream"),
    ):
        if body.get(theirs) is not None:
            openai_body[ours] = body[theirs]

    if body.get("stop_sequences"):
        openai_body["stop"] = body["stop_sequences"]

    return openai_body


def response_to_anthropic(response: Dict[str, Any]) -> Dict[str, Any]:
    """An OpenAI chat completion as an Anthropic Message."""
    if not isinstance(response, dict):
        return response

    choices = response.get("choices") or [{}]
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") or {}
    usage = response.get("usage") or {}

    return {
        "id": response.get("id", ""),
        "type": "message",
        "role": "assistant",
        "model": response.get("model", ""),
        "content": [{"type": "text", "text": message.get("content") or ""}],
        "stop_reason": _FINISH_TO_STOP.get(choice.get("finish_reason"), "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


class StreamTranslator:
    """OpenAI stream chunks as Anthropic stream events.

    One instance per request, so instance state is safe here. Provider adapters
    cannot do this because they are cached and shared; a surface translator is
    built fresh for each client.

    Anthropic wraps its text in an explicit envelope: a message opens, a content
    block opens, text arrives as deltas, then both close in order. OpenAI
    carries all of that on one chunk shape, so most events here are synthesised
    rather than translated.
    """

    def __init__(self, model: str = "") -> None:
        self._model = model
        self._id = f"msg_{uuid.uuid4().hex[:24]}"
        self._opened = False
        self._stop_reason = "end_turn"
        self._deltas = 0

    @staticmethod
    def _sse(event_type: str, data: Dict[str, Any]) -> bytes:
        """Anthropic names the event type on its own line as well as in the body."""
        payload = json.dumps(data, separators=(",", ":"))
        return f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8")

    def _open(self) -> List[bytes]:
        """The envelope Anthropic sends before any text."""
        self._opened = True
        return [
            self._sse("message_start", {
                "type": "message_start",
                "message": {
                    "id": self._id,
                    "type": "message",
                    "role": "assistant",
                    "model": self._model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            }),
            self._sse("content_block_start", {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }),
        ]

    def chunk(self, chunk: Dict[str, Any]) -> List[bytes]:
        """Events for one OpenAI chunk. The envelope opens on the first one."""
        out: List[bytes] = []

        if self._model == "" and chunk.get("model"):
            self._model = chunk["model"]

        choices = chunk.get("choices") or []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}
        text = delta.get("content") or ""

        if choice.get("finish_reason"):
            self._stop_reason = _FINISH_TO_STOP.get(
                choice["finish_reason"], "end_turn"
            )

        if not text:
            return out

        if not self._opened:
            out.extend(self._open())
        self._deltas += 1
        out.append(self._sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        }))
        return out

    def finish(self) -> List[bytes]:
        """Close the envelope. Emitted whether or not any text arrived."""
        out: List[bytes] = []
        if not self._opened:
            out.extend(self._open())
        out.append(self._sse("content_block_stop", {
            "type": "content_block_stop", "index": 0,
        }))
        out.append(self._sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": self._stop_reason, "stop_sequence": None},
            # Deltas, not tokens. Upstream only reports an exact count when
            # `stream_options.include_usage` is requested, which the surface
            # does not control. One delta is one token on vLLM and an
            # approximation anywhere that batches.
            "usage": {"output_tokens": self._deltas},
        }))
        out.append(self._sse("message_stop", {"type": "message_stop"}))
        return out


def iter_openai_events(text: str) -> Iterable[Optional[Dict[str, Any]]]:
    """The JSON payload of each `data:` line, or None for the `[DONE]` sentinel."""
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if not payload:
            continue
        if payload == "[DONE]":
            yield None
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue
