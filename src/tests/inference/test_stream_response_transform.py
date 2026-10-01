"""Streaming responses must be translated to OpenAI format, like non-streaming ones.

`call_upstream` ran the adapter's `transform_response`; `stream_upstream` yielded
raw chunks. So a provider with its own SSE format answered a streaming request in
that format, while the same request non-streaming came back translated.

Anthropic is the case these tests use: it opens with a message envelope, streams
text as content-block deltas, closes with a stop reason, and never sends the
`[DONE]` sentinel an OpenAI client waits for.
"""
import json

import pytest

from inference.core.providers.external.anthropic import AnthropicAdapter
from inference.core.providers.external.openai import OpenAIAdapter
from inference.core.stream_processor import StreamProcessor

MSG_ID = "msg_01ABC"
MODEL = "claude-sonnet-4"


def _anthropic_stream():
    """The event sequence Anthropic actually sends, `event:` lines included."""
    events = [
        ("message_start", {
            "type": "message_start",
            "message": {"id": MSG_ID, "model": MODEL, "role": "assistant",
                        "usage": {"input_tokens": 11}},
        }),
        ("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""},
        }),
        ("ping", {"type": "ping"}),
        ("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        }),
        ("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": " world"},
        }),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 7},
        }),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(
        f"event: {name}\ndata: {json.dumps(body)}\n\n" for name, body in events
    ).encode("utf-8")


async def _collect(chunks, adapter):
    async def _gen():
        for c in chunks:
            yield c

    out = b""
    async for piece in StreamProcessor.process_stream(
        _gen(), start_time=0.0, usage_tracker={}, adapter=adapter
    ):
        out += piece
    return out.decode("utf-8")


def _events(text):
    """The JSON payload of every `data:` line that is not the sentinel."""
    return [
        json.loads(line[6:])
        for line in text.split("\n")
        if line.startswith("data: ") and line[6:].strip() not in ("", "[DONE]")
    ]


@pytest.mark.asyncio
class TestAnthropicStreamIsTranslated:

    async def test_every_event_becomes_an_openai_chunk(self):
        got = await _collect([_anthropic_stream()], AnthropicAdapter())
        for event in _events(got):
            assert event["object"] == "chat.completion.chunk"
            assert "choices" in event

    async def test_the_text_survives_in_order(self):
        got = await _collect([_anthropic_stream()], AnthropicAdapter())
        text = "".join(
            e["choices"][0]["delta"].get("content", "") for e in _events(got)
        )
        assert text == "Hello world"

    async def test_the_role_opens_the_stream(self):
        got = await _collect([_anthropic_stream()], AnthropicAdapter())
        assert _events(got)[0]["choices"][0]["delta"] == {"role": "assistant"}

    async def test_the_stop_reason_is_mapped(self):
        got = await _collect([_anthropic_stream()], AnthropicAdapter())
        assert _events(got)[-1]["choices"][0]["finish_reason"] == "stop"

    async def test_done_is_synthesised(self):
        """Anthropic never sends it, and an OpenAI client waits for it."""
        got = await _collect([_anthropic_stream()], AnthropicAdapter())
        assert got.rstrip().endswith("data: [DONE]")

    async def test_id_and_model_carry_across_events(self):
        """Only message_start names them, so later chunks need the stream state."""
        got = await _collect([_anthropic_stream()], AnthropicAdapter())
        assert {e["id"] for e in _events(got)} == {MSG_ID}
        assert {e["model"] for e in _events(got)} == {MODEL}

    async def test_anthropic_event_types_do_not_leak(self):
        """The client is given a fresh OpenAI stream, not Anthropic's."""
        got = await _collect([_anthropic_stream()], AnthropicAdapter())
        for leaked in ("event: ", "content_block", "message_start", "ping"):
            assert leaked not in got, leaked

    async def test_events_with_no_openai_equivalent_are_dropped(self):
        """ping, content_block_start/stop and message_stop carry nothing."""
        got = await _collect([_anthropic_stream()], AnthropicAdapter())
        # role, two text deltas, stop reason.
        assert len(_events(got)) == 4


@pytest.mark.asyncio
class TestChunkBoundaries:
    """`aiter_raw` gives byte fragments, so an event can split anywhere."""

    async def test_split_mid_event(self):
        payload = _anthropic_stream()
        half = len(payload) // 2
        got = await _collect([payload[:half], payload[half:]], AnthropicAdapter())
        text = "".join(
            e["choices"][0]["delta"].get("content", "") for e in _events(got)
        )
        assert text == "Hello world"

    async def test_one_byte_at_a_time(self):
        payload = _anthropic_stream()
        got = await _collect(
            [payload[i:i + 1] for i in range(len(payload))], AnthropicAdapter()
        )
        text = "".join(
            e["choices"][0]["delta"].get("content", "") for e in _events(got)
        )
        assert text == "Hello world"
        assert got.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
class TestOpenAIShapedProvidersAreUntouched:
    """The default must leave every existing provider exactly as it was."""

    async def test_bytes_pass_through_unchanged(self):
        payload = (
            b'data: {"id":"1","object":"chat.completion.chunk",'
            b'"choices":[{"delta":{"content":"hi"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        got = await _collect([payload], OpenAIAdapter())
        assert got == payload.decode("utf-8")

    async def test_sse_comments_survive(self):
        """They are keepalives; dropping them can idle a connection out."""
        payload = b": keep-alive\n\ndata: [DONE]\n\n"
        got = await _collect([payload], OpenAIAdapter())
        assert ": keep-alive" in got

    async def test_no_done_is_invented(self):
        payload = b'data: {"id":"1"}\n\n'
        got = await _collect([payload], OpenAIAdapter())
        assert "[DONE]" not in got


@pytest.mark.asyncio
async def test_a_failing_translator_drops_the_event_and_keeps_the_stream():
    """Passing the untranslated event through would hand the client a chunk it
    cannot parse. Raising would discard a response already half-read."""
    class Broken(AnthropicAdapter):
        def transform_stream_event(self, event, state):
            raise RuntimeError("translator blew up")

    got = await _collect([_anthropic_stream()], Broken())

    assert got.rstrip().endswith("data: [DONE]"), "the stream still completes"
    assert _events(got) == [], "nothing untranslated reached the client"
    for leaked in ("content_block", "message_start", "ping"):
        assert leaked not in got, leaked


def test_state_is_not_kept_on_the_adapter():
    """Adapters are cached module-level and shared by concurrent requests."""
    adapter = AnthropicAdapter()
    first, second = {}, {}

    adapter.transform_stream_event(
        {"type": "message_start", "message": {"id": "a", "model": "m1"}}, first
    )
    adapter.transform_stream_event(
        {"type": "message_start", "message": {"id": "b", "model": "m2"}}, second
    )

    assert first["id"] == "a" and second["id"] == "b"
    assert not hasattr(adapter, "id")
