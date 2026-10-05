"""Streaming token counts must come from the engine, not from an estimate.

vLLM reports usage on a stream only when `stream_options.include_usage` is set.
Nothing asked for it, so every streaming request fell back to counting tokens in
the message text — which misses the chat template entirely. Measured on one
request: 10 prompt tokens recorded against the engine's 35.

These numbers are what `check_quota` counts against, so the estimate was not
only wrong, it was wrong in the direction that undercharges.

The flag produces a final chunk carrying usage and no choices. The client did not
ask for that chunk, so it is read and dropped rather than forwarded.
"""
import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from inference.core.stream_processor import StreamProcessor


def _line(event) -> str:
    return "data: " + json.dumps(event)


def _content_chunk(text):
    return {
        "id": "chatcmpl-1", "model": "upstream-name",
        "choices": [{"index": 0, "delta": {"content": text}}],
    }


def _usage_chunk(prompt=35, completion=86):
    """What upstream sends last when include_usage is on: usage, no choices."""
    return {
        "id": "chatcmpl-1", "model": "upstream-name", "choices": [],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


class TestTheUsageChunkIsDropped:
    """Asking upstream for usage must not change what the client receives."""

    def test_it_is_dropped_when_the_gateway_asked(self):
        got = StreamProcessor._transform_line(
            _line(_usage_chunk()), "dep-1", None, {}, True
        )
        assert got is None

    def test_it_is_forwarded_when_the_client_asked(self):
        """Then it is their chunk, and removing it would break their accounting."""
        got = StreamProcessor._transform_line(
            _line(_usage_chunk()), "dep-1", None, {}, False
        )
        assert got is not None
        assert json.loads(got[len("data: "):])["usage"]["prompt_tokens"] == 35

    def test_a_content_chunk_is_never_dropped(self):
        got = StreamProcessor._transform_line(
            _line(_content_chunk("hello")), "dep-1", None, {}, True
        )
        assert got is not None
        assert "hello" in got

    def test_a_chunk_with_usage_and_content_is_kept(self):
        """Some engines attach usage to a chunk that also carries a token."""
        event = _content_chunk("hi")
        event["usage"] = {"prompt_tokens": 35, "completion_tokens": 1}
        got = StreamProcessor._transform_line(_line(event), "dep-1", None, {}, True)
        assert got is not None
        assert "hi" in got

    def test_the_done_sentinel_survives(self):
        assert StreamProcessor._transform_line(
            "data: [DONE]", "dep-1", None, {}, True
        ) == "data: [DONE]"


class TestTheNumbersStillArrive:
    """Dropping the chunk must not drop the counts it carried."""

    def test_usage_is_read_from_the_raw_chunk(self):
        """`_parse_usage` runs on the bytes, before any line is dropped."""
        tracker = {"prompt_tokens": 10, "completion_tokens": 0}
        payload = (_line(_usage_chunk(35, 86)) + "\n\n").encode("utf-8")

        StreamProcessor._parse_usage(payload, tracker, "")

        assert tracker["prompt_tokens"] == 35
        assert tracker["completion_tokens"] == 86

    def test_upstream_overrides_the_estimate(self):
        """The estimate is a placeholder; the engine's number replaces it."""
        tracker = {"prompt_tokens": 10, "completion_tokens": 7}
        payload = (_line(_usage_chunk(35, 86)) + "\n\n").encode("utf-8")

        StreamProcessor._parse_usage(payload, tracker, "")

        assert tracker["_provider_usage_seen"] is True
        assert tracker["prompt_tokens"] != 10


@pytest.mark.asyncio
class TestWhatIsSentUpstream:
    """The flag has to reach the engine, and only when the client did not set it."""

    @staticmethod
    async def _payload_sent(body):
        from inference.core.handlers import completion as ch
        from inference.core.providers.external.openai import OpenAIAdapter

        seen = {}

        async def _log(**kwargs):
            pass

        def _stream(url, payload, headers, engine, **kw):
            seen["payload"] = payload

            async def _gen():
                yield b'data: {}\n\n'

            return _gen()

        with patch.object(ch.RequestLogger, "log", _log), \
             patch.object(ch.GatewayService, "stream_upstream", _stream):
            response = ch.CompletionHandler._handle_streaming(
                "http://up.test", body, {}, "vllm", OpenAIAdapter(),
                "dep-1", "user-1", "m", {}, 0.0, MagicMock(),
                [], False, "10.0.0.1", "key",
            )
            async for _ in response.body_iterator:
                pass
            await asyncio.sleep(0)
        return seen["payload"]

    async def test_a_streaming_request_asks_the_engine_to_count(self):
        payload = await self._payload_sent({
            "model": "m", "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert payload["stream_options"] == {"include_usage": True}

    async def test_a_client_that_turned_usage_off_stays_off(self):
        """Overriding it would change a response the caller deliberately shaped."""
        payload = await self._payload_sent({
            "model": "m", "stream": True,
            "stream_options": {"include_usage": False},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert payload["stream_options"] == {"include_usage": False}
