"""The streamed response must echo back the model name the client sent.

Clients address a deployment by name ("vllm-14b") but upstream answers with
its own id ("Qwen/Qwen2.5-14B-Instruct-AWQ"), and some OpenAI client libraries
assert the two match.

Upstream is read with `aiter_raw`, so chunks are arbitrary byte fragments: a
single `data:` line, or even the model name inside it, can arrive split across
two chunks. Those splits are what these tests are mostly about, because a
rewrite that only works on tidy line-aligned chunks passes a casual test and
fails in production.
"""
import json

import pytest

from inference.core.stream_processor import StreamProcessor
from inference.core.worker_routing import echo_requested_model

UPSTREAM = "Qwen/Qwen2.5-14B-Instruct-AWQ"
REQUESTED = "vllm-14b"


def _event(content="hi"):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "model": UPSTREAM,
        "choices": [{"index": 0, "delta": {"content": content}}],
    }


def _sse(*events):
    """Serialise events the way vLLM does, ending with the DONE sentinel."""
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
    return body + "data: [DONE]\n\n"


async def _collect(chunks, rewrite_model=REQUESTED):
    async def _gen():
        for c in chunks:
            yield c

    out = b""
    async for piece in StreamProcessor.process_stream(
        _gen(), start_time=0.0, usage_tracker={}, rewrite_model=rewrite_model
    ):
        out += piece
    return out.decode("utf-8")


def _models_in(text):
    return [
        json.loads(line[6:])["model"]
        for line in text.split("\n")
        if line.startswith("data: ") and line[6:].strip() not in ("", "[DONE]")
    ]


@pytest.mark.asyncio
class TestRewrite:

    async def test_model_is_replaced(self):
        got = await _collect([_sse(_event()).encode()])
        assert _models_in(got) == [REQUESTED]
        assert UPSTREAM not in got

    async def test_every_event_is_replaced(self):
        payload = _sse(_event("a"), _event("b"), _event("c"))
        got = await _collect([payload.encode()])
        assert _models_in(got) == [REQUESTED] * 3

    async def test_done_sentinel_survives(self):
        got = await _collect([_sse(_event()).encode()])
        assert got.endswith("data: [DONE]\n\n")

    async def test_content_is_untouched(self):
        got = await _collect([_sse(_event("hello world")).encode()])
        event = json.loads(got.split("\n")[0][6:])
        assert event["choices"][0]["delta"]["content"] == "hello world"


@pytest.mark.asyncio
class TestChunkBoundaries:
    """`aiter_raw` gives byte fragments, not lines."""

    async def test_line_split_across_two_chunks(self):
        payload = _sse(_event()).encode()
        half = len(payload) // 2
        got = await _collect([payload[:half], payload[half:]])
        assert _models_in(got) == [REQUESTED]
        assert UPSTREAM not in got

    async def test_split_inside_the_model_name(self):
        """The case a naive byte replace gets wrong."""
        payload = _sse(_event()).encode()
        cut = payload.index(UPSTREAM.encode()) + 5
        got = await _collect([payload[:cut], payload[cut:]])
        assert _models_in(got) == [REQUESTED]
        assert UPSTREAM not in got

    async def test_a_multibyte_character_split_across_chunks_survives(self):
        """Rewriting decodes bytes to text, so the decode must be incremental.

        A per-chunk decode with errors="ignore" drops the leading bytes of a
        character straddling the boundary and eats it from the response.
        """
        text = "नमस्ते"  # Devanagari, 3 bytes per char
        event = _event(text)
        payload = (f"data: {json.dumps(event, ensure_ascii=False)}\n\n").encode("utf-8")
        cut = payload.index(text.encode("utf-8")) + 1  # slice inside a character

        got = await _collect([payload[:cut], payload[cut:]])
        event_out = json.loads(got.split("\n")[0][6:])
        assert event_out["choices"][0]["delta"]["content"] == text

    async def test_one_byte_at_a_time(self):
        payload = _sse(_event("x"), _event("y")).encode()
        got = await _collect([payload[i:i + 1] for i in range(len(payload))])
        assert _models_in(got) == [REQUESTED] * 2
        assert got.endswith("data: [DONE]\n\n")

    async def test_a_line_with_no_trailing_newline_is_still_emitted(self):
        """Upstream can end without a final newline; nothing may be swallowed."""
        payload = f"data: {json.dumps(_event())}".encode()
        got = await _collect([payload])
        assert _models_in(got) == [REQUESTED]


@pytest.mark.asyncio
class TestPassThrough:
    """Anything that is not a JSON data event must survive byte for byte."""

    async def test_non_json_line(self):
        got = await _collect([b"data: not json at all\n\n"])
        assert got == "data: not json at all\n\n"

    async def test_comment_line(self):
        got = await _collect([b": keep-alive\n\n"])
        assert got == ": keep-alive\n\n"

    async def test_event_without_a_model_field(self):
        raw = 'data: {"id":"1","object":"chunk"}\n\n'
        got = await _collect([raw.encode()])
        assert json.loads(got.split("\n")[0][6:]) == {"id": "1", "object": "chunk"}

    async def test_crlf_endings_are_preserved(self):
        raw = f"data: {json.dumps(_event())}\r\n\r\n".encode()
        got = await _collect([raw])
        assert "\r\n" in got
        assert _models_in(got.replace("\r", "")) == [REQUESTED]

    async def test_no_rewrite_requested_yields_bytes_unchanged(self):
        """The default path must behave exactly as before."""
        payload = _sse(_event()).encode()
        got = await _collect([payload], rewrite_model=None)
        assert got == payload.decode("utf-8")


@pytest.mark.asyncio
async def test_usage_tracking_still_works_while_rewriting():
    """Rewriting re-frames the stream, so the usage parser must be unaffected."""
    event = _event("hi")
    event["usage"] = {"prompt_tokens": 11, "completion_tokens": 7}
    payload = _sse(event).encode()

    async def _gen():
        # Split mid-line so both the parser and the framer see fragments.
        yield payload[:20]
        yield payload[20:]

    tracker = {}
    async for _ in StreamProcessor.process_stream(
        _gen(), start_time=0.0, usage_tracker=tracker, rewrite_model=REQUESTED
    ):
        pass

    assert tracker["prompt_tokens"] == 11
    assert tracker["completion_tokens"] == 7
    assert tracker.get("ttft_ms") is not None


@pytest.mark.asyncio
class TestStandardResponse:
    """The non-streaming path has the same contract."""

    @staticmethod
    async def _run(monkeypatch, upstream_response):
        from unittest.mock import MagicMock

        from inference.core.handlers import completion as ch

        async def _call_upstream(*_a, **_k):
            return upstream_response

        monkeypatch.setattr(ch.GatewayService, "call_upstream", _call_upstream)
        return await ch.CompletionHandler._handle_standard(
            "http://upstream.test", {}, {}, "vllm", "dep-1", "user-1",
            REQUESTED, {}, 0.0, MagicMock(), [], False, "10.0.0.1", "key",
        )

    async def test_model_is_echoed_back(self, monkeypatch):
        got = await self._run(
            monkeypatch,
            {"id": "1", "model": UPSTREAM, "usage": {}, "choices": []},
        )
        assert got["model"] == REQUESTED

    async def test_the_rest_of_the_body_is_untouched(self, monkeypatch):
        got = await self._run(
            monkeypatch,
            {
                "id": "chatcmpl-9",
                "model": UPSTREAM,
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
                "choices": [{"message": {"content": "hi"}}],
            },
        )
        assert got["id"] == "chatcmpl-9"
        assert got["usage"] == {"prompt_tokens": 3, "completion_tokens": 4}
        assert got["choices"][0]["message"]["content"] == "hi"

    async def test_a_response_without_a_model_field_gains_none(self, monkeypatch):
        """Don't invent a field upstream did not send."""
        got = await self._run(monkeypatch, {"id": "1", "usage": {}})
        assert "model" not in got


@pytest.mark.asyncio
class TestEmbeddingResponse:
    """`/v1/embeddings` carries a model field too, and had the same mismatch."""

    @staticmethod
    async def _run(monkeypatch, upstream_response):
        from unittest.mock import AsyncMock, MagicMock

        from inference.core.handlers import embedding as eh

        async def _call_upstream(*_a, **_k):
            return upstream_response

        monkeypatch.setattr(eh.GatewayService, "call_upstream", _call_upstream)
        for step in ("resolve_context", "check_rate_limit", "check_quota"):
            monkeypatch.setattr(eh.Pipeline, step, AsyncMock())

        def _resolve_provider(ctx, default_engine="infinity"):
            ctx.engine = "infinity"
            ctx.endpoint_url = "http://upstream.test"
            ctx.provider_headers = {}
            ctx.adapter = MagicMock()
            ctx.adapter.transform_request.side_effect = lambda p: p
            ctx.adapter.get_endpoint_path.return_value = "/v1/embeddings"
            # What the real pipeline does: the upstream id replaces the
            # client's name in the outgoing body, never on ctx.model.
            ctx.body["model"] = UPSTREAM

        monkeypatch.setattr(eh.Pipeline, "resolve_provider", _resolve_provider)

        return await eh.EmbeddingHandler.handle(
            api_key="sk-test",
            body={"model": REQUESTED, "input": ["hello"]},
            background_tasks=MagicMock(),
        )

    async def test_model_is_echoed_back(self, monkeypatch):
        got = await self._run(
            monkeypatch,
            {"object": "list", "model": UPSTREAM, "data": [], "usage": {}},
        )
        assert got["model"] == REQUESTED

    async def test_embeddings_are_untouched(self, monkeypatch):
        got = await self._run(
            monkeypatch,
            {
                "object": "list",
                "model": UPSTREAM,
                "data": [{"index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 2},
            },
        )
        assert got["data"] == [{"index": 0, "embedding": [0.1, 0.2]}]
        assert got["usage"] == {"prompt_tokens": 2}


class TestEchoRequestedModel:
    """The guard lives in one place now, so it is tested in one place."""

    def test_replaces_the_model(self):
        got = echo_requested_model({"model": UPSTREAM}, REQUESTED)
        assert got == {"model": REQUESTED}

    def test_leaves_a_response_without_a_model_alone(self):
        """Engines like image generation send no model; don't invent one."""
        got = echo_requested_model({"created": 1, "data": []}, REQUESTED)
        assert "model" not in got

    def test_other_fields_are_untouched(self):
        got = echo_requested_model(
            {"model": UPSTREAM, "usage": {"prompt_tokens": 3}, "data": [1, 2]},
            REQUESTED,
        )
        assert got["usage"] == {"prompt_tokens": 3}
        assert got["data"] == [1, 2]

    def test_a_non_dict_body_passes_through(self):
        assert echo_requested_model([1, 2], REQUESTED) == [1, 2]
        assert echo_requested_model(None, REQUESTED) is None

    def test_the_same_object_is_returned(self):
        """Callers `return` the result, so it must not be a copy."""
        body = {"model": UPSTREAM}
        assert echo_requested_model(body, REQUESTED) is body
