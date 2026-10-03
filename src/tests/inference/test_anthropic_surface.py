"""`/v1/messages`, the Anthropic Messages API as a client-facing surface.

Anything written against the Claude SDK could not point at the gateway at all,
because only OpenAI-shaped routes existed. The surface translates in and out
around the ordinary completion path, so quotas, rate limiting, routing and
logging are shared rather than reimplemented.

These tests pin the translation in both directions. The round trip matters more
than any single field: a client sends Anthropic and must receive Anthropic,
whatever the engine speaks in between.
"""
import json

import pytest

from inference.core.surfaces import anthropic as surface


class TestRequestIn:
    """Anthropic request -> OpenAI request."""

    def test_system_becomes_a_message(self):
        """Anthropic carries the system prompt beside the messages, not in them."""
        got = surface.request_to_openai({
            "model": "m", "system": "be terse",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert got["messages"][0] == {"role": "system", "content": "be terse"}
        assert got["messages"][1] == {"role": "user", "content": "hi"}

    def test_no_system_means_no_extra_message(self):
        got = surface.request_to_openai({
            "model": "m", "messages": [{"role": "user", "content": "hi"}],
        })
        assert [m["role"] for m in got["messages"]] == ["user"]

    def test_content_blocks_are_flattened(self):
        """Anthropic content is a string or a list of typed blocks."""
        got = surface.request_to_openai({
            "model": "m",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "one "},
                {"type": "text", "text": "two"},
            ]}],
        })
        assert got["messages"][0]["content"] == "one two"

    def test_stop_sequences_become_stop(self):
        got = surface.request_to_openai({
            "model": "m", "messages": [], "stop_sequences": ["END"],
        })
        assert got["stop"] == ["END"]

    def test_sampling_fields_carry_over(self):
        got = surface.request_to_openai({
            "model": "m", "messages": [],
            "max_tokens": 128, "temperature": 0.2, "top_p": 0.9, "stream": True,
        })
        assert got["max_tokens"] == 128
        assert got["temperature"] == 0.2
        assert got["top_p"] == 0.9
        assert got["stream"] is True

    def test_absent_fields_are_not_invented(self):
        """Sending temperature=None downstream is not the same as omitting it."""
        got = surface.request_to_openai({"model": "m", "messages": []})
        for absent in ("temperature", "top_p", "max_tokens", "stream", "stop"):
            assert absent not in got


class TestResponseOut:
    """OpenAI response -> Anthropic Message."""

    def _openai(self, content="hello", finish="stop"):
        return {
            "id": "chatcmpl-1", "model": "m",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish,
            }],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }

    def test_shape(self):
        got = surface.response_to_anthropic(self._openai())
        assert got["type"] == "message"
        assert got["role"] == "assistant"
        assert got["content"] == [{"type": "text", "text": "hello"}]

    def test_usage_is_renamed(self):
        got = surface.response_to_anthropic(self._openai())
        assert got["usage"] == {"input_tokens": 11, "output_tokens": 7}
        assert "prompt_tokens" not in json.dumps(got)

    @pytest.mark.parametrize("finish,stop", [
        ("stop", "end_turn"),
        ("length", "max_tokens"),
        ("tool_calls", "tool_use"),
        (None, "end_turn"),
    ])
    def test_finish_reason_is_mapped(self, finish, stop):
        got = surface.response_to_anthropic(self._openai(finish=finish))
        assert got["stop_reason"] == stop

    def test_an_empty_response_does_not_raise(self):
        assert surface.response_to_anthropic({})["content"] == [
            {"type": "text", "text": ""}
        ]


def _events(frames):
    """(event type, body) for each SSE frame the translator emitted."""
    out = []
    for frame in frames:
        text = frame.decode("utf-8")
        etype = text.split("\n")[0][len("event: "):]
        body = json.loads(text.split("data: ", 1)[1].strip())
        out.append((etype, body))
    return out


class TestStreamOut:
    """OpenAI stream chunks -> Anthropic stream events."""

    def _chunk(self, content=None, finish=None):
        return {
            "id": "chatcmpl-1", "object": "chat.completion.chunk", "model": "m",
            "choices": [{
                "index": 0,
                "delta": {"content": content} if content else {},
                "finish_reason": finish,
            }],
        }

    def _run(self, chunks):
        t = surface.StreamTranslator(model="m")
        frames = []
        for c in chunks:
            frames.extend(t.chunk(c))
        frames.extend(t.finish())
        return _events(frames)

    def test_the_envelope_is_in_order(self):
        """Anthropic opens a message, opens a block, then closes both."""
        got = [etype for etype, _ in self._run([
            self._chunk("Hello"), self._chunk(" world"), self._chunk(finish="stop"),
        ])]
        assert got == [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "message_stop",
        ]

    def test_the_text_survives_in_order(self):
        events = self._run([self._chunk("Hello"), self._chunk(" world")])
        text = "".join(
            body["delta"]["text"]
            for etype, body in events if etype == "content_block_delta"
        )
        assert text == "Hello world"

    def test_the_envelope_opens_only_once(self):
        events = self._run([self._chunk("a"), self._chunk("b"), self._chunk("c")])
        starts = [e for e, _ in events if e == "message_start"]
        assert len(starts) == 1

    def test_the_stop_reason_reaches_message_delta(self):
        events = self._run([self._chunk("hi"), self._chunk(finish="length")])
        delta = next(b for e, b in events if e == "message_delta")
        assert delta["delta"]["stop_reason"] == "max_tokens"

    def test_an_empty_stream_still_closes(self):
        """A client waits on message_stop; it must arrive even with no text."""
        got = [e for e, _ in self._run([])]
        assert got[0] == "message_start"
        assert got[-1] == "message_stop"

    def test_chunks_with_no_content_emit_nothing(self):
        """The role-only opening chunk carries no text."""
        t = surface.StreamTranslator(model="m")
        assert t.chunk({"choices": [{"delta": {"role": "assistant"}}]}) == []

    def test_every_frame_names_its_event_type(self):
        """Anthropic clients read the `event:` line, not just the body."""
        for frame in surface.StreamTranslator("m").finish():
            text = frame.decode("utf-8")
            assert text.startswith("event: ")
            assert "\ndata: " in text
            assert text.endswith("\n\n")


class TestStreamUsage:
    """`output_tokens` is what quota reads, so it has to be upstream's number."""

    def _chunk(self, content=None, finish=None):
        return {
            "model": "m",
            "choices": [{
                "index": 0,
                "delta": {"content": content} if content else {},
                "finish_reason": finish,
            }],
        }

    def _usage_chunk(self, prompt, completion):
        """What upstream sends last when include_usage is on: no choices."""
        return {
            "model": "m",
            "choices": [],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        }

    def _message_delta(self, chunks):
        t = surface.StreamTranslator(model="m")
        frames = []
        for c in chunks:
            frames.extend(t.chunk(c))
        frames.extend(t.finish())
        return next(b for e, b in _events(frames) if e == "message_delta")

    def test_a_streaming_request_asks_upstream_to_count(self):
        body = surface.request_to_openai({
            "model": "m", "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert body["stream_options"] == {"include_usage": True}

    def test_a_non_streaming_request_does_not(self):
        body = surface.request_to_openai({
            "model": "m", "messages": [{"role": "user", "content": "hi"}],
        })
        assert "stream_options" not in body

    def test_output_tokens_are_upstreams_not_the_delta_count(self):
        """Three deltas, upstream says six. Six is the answer.

        The gap is real: the role chunk, the finish_reason chunk and the stop
        token carry no text, so none of them is a delta.
        """
        delta = self._message_delta([
            self._chunk("a"), self._chunk("b"), self._chunk("c"),
            self._chunk(finish="stop"),
            self._usage_chunk(10, 6),
        ])
        assert delta["usage"]["output_tokens"] == 6

    def test_the_delta_count_is_only_a_fallback(self):
        """An engine that reports nothing still gets an approximate number."""
        delta = self._message_delta([self._chunk("a"), self._chunk("b")])
        assert delta["usage"]["output_tokens"] == 2

    def test_the_usage_chunk_emits_no_text(self):
        """It carries no choices, so it must not open a block or add a delta."""
        t = surface.StreamTranslator(model="m")
        assert t.chunk(self._usage_chunk(10, 6)) == []

    def test_a_zero_count_from_upstream_is_still_upstreams(self):
        """Falling back on 0 would report deltas for an engine that said zero."""
        delta = self._message_delta([
            self._chunk("a"), self._usage_chunk(10, 0),
        ])
        assert delta["usage"]["output_tokens"] == 0


class TestEventParsing:

    def test_done_is_reported_as_none(self):
        got = list(surface.iter_openai_events("data: [DONE]\n\n"))
        assert got == [None]

    def test_non_json_is_skipped_not_raised(self):
        got = list(surface.iter_openai_events("data: not json\n\n"))
        assert got == []

    def test_comments_and_blanks_are_skipped(self):
        got = list(surface.iter_openai_events(": keep-alive\n\n\n"))
        assert got == []


@pytest.mark.asyncio
async def test_the_stream_closes_even_if_upstream_dies():
    """A reader is otherwise left waiting on message_stop forever."""
    from inference.core.handlers.messages import MessagesHandler

    async def _broken():
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise RuntimeError("upstream died")

    translator = surface.StreamTranslator(model="m")
    seen = []
    with pytest.raises(RuntimeError):
        async for frame in MessagesHandler._translate_stream(_broken(), translator):
            seen.append(frame)

    assert b"message_stop" in b"".join(seen)


@pytest.mark.asyncio
class TestHandlerWiring:
    """The surface must sit around the ordinary path, not beside it."""

    @staticmethod
    async def _run(monkeypatch, body, upstream):
        from unittest.mock import MagicMock

        from inference.core.handlers import messages as mh

        seen = {}

        async def _handle(**kwargs):
            seen.update(kwargs)
            return upstream

        monkeypatch.setattr(mh.CompletionHandler, "handle", _handle)
        got = await mh.MessagesHandler.handle(
            api_key="sk-test", body=body, background_tasks=MagicMock(),
        )
        return got, seen

    async def test_the_completion_path_receives_openai_shape(self, monkeypatch):
        _, seen = await self._run(
            monkeypatch,
            {"model": "m", "system": "be terse",
             "messages": [{"role": "user", "content": "hi"}]},
            {"choices": [{"message": {"content": "ok"}}], "usage": {}},
        )
        assert seen["body"]["messages"][0]["role"] == "system"
        assert "system" not in seen["body"]

    async def test_the_client_receives_anthropic_shape(self, monkeypatch):
        got, _ = await self._run(
            monkeypatch,
            {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            {"id": "chatcmpl-1", "model": "m",
             "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 3, "completion_tokens": 2}},
        )
        assert got["type"] == "message"
        assert got["content"] == [{"type": "text", "text": "ok"}]
        assert got["usage"] == {"input_tokens": 3, "output_tokens": 2}

    async def test_the_api_key_is_passed_straight_through(self, monkeypatch):
        _, seen = await self._run(
            monkeypatch, {"model": "m", "messages": []},
            {"choices": [{"message": {"content": ""}}], "usage": {}},
        )
        assert seen["api_key"] == "sk-test"


@pytest.mark.asyncio
class TestTheRealAnthropicSDK:
    """The surface parsed by the client library it exists for.

    Everything above tests our own understanding of the format. This tests
    Anthropic's. Skipped when the SDK is absent so CI does not require it.
    """

    @staticmethod
    def _client(app):
        httpx2 = pytest.importorskip("httpx2")
        anthropic = pytest.importorskip("anthropic")
        return anthropic.AsyncAnthropic(
            api_key="sk-test",
            base_url="http://testserver",
            http_client=httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://testserver",
            ),
        )

    @staticmethod
    def _patch(monkeypatch, result):
        from inference.core.handlers import messages as mh

        async def _handle(**kwargs):
            return result(kwargs) if callable(result) else result

        monkeypatch.setattr(mh.CompletionHandler, "handle", _handle)

    async def test_a_message_round_trips(self, monkeypatch):
        from inference.app import app

        self._patch(monkeypatch, {
            "id": "chatcmpl-1", "model": "m",
            "choices": [{"message": {"role": "assistant", "content": "Paris."},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 14, "completion_tokens": 6},
        })

        msg = await self._client(app).messages.create(
            model="m", max_tokens=100,
            messages=[{"role": "user", "content": "capital of France?"}],
        )
        assert msg.content[0].text == "Paris."
        assert msg.stop_reason == "end_turn"
        assert msg.usage.input_tokens == 14

    async def test_a_stream_round_trips(self, monkeypatch):
        """text_stream only reassembles if the whole envelope is well ordered,
        and get_final_message only returns once message_stop arrives."""
        from fastapi.responses import StreamingResponse

        from inference.app import app

        chunks = [
            b'data: {"model":"m","choices":[{"delta":{"role":"assistant"}}]}\n\n',
            b'data: {"model":"m","choices":[{"delta":{"content":"Pa"}}]}\n\n',
            b'data: {"model":"m","choices":[{"delta":{"content":"ris."}}]}\n\n',
            b'data: {"model":"m","choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b"data: [DONE]\n\n",
        ]

        async def _gen():
            for c in chunks:
                yield c

        self._patch(
            monkeypatch,
            lambda _: StreamingResponse(_gen(), media_type="text/event-stream"),
        )

        text = ""
        async with self._client(app).messages.stream(
            model="m", max_tokens=100,
            messages=[{"role": "user", "content": "capital of France?"}],
        ) as stream:
            async for token in stream.text_stream:
                text += token
            final = await stream.get_final_message()

        assert text == "Paris."
        assert final.stop_reason == "end_turn"


class TestApiKeyExtraction:
    """Anthropic clients authenticate differently, but not more loosely."""

    def _call(self, headers, monkeypatch, sandbox_return="sandbox:org:user"):
        import importlib

        from fastapi.testclient import TestClient

        from inference.core.handlers import messages as mh

        # `inference.app` the attribute is the FastAPI object, not the module.
        appmod = importlib.import_module("inference.app")

        seen = {}

        async def _handle(**kwargs):
            seen.update(kwargs)
            return {"choices": [{"message": {"content": ""}}], "usage": {}}

        monkeypatch.setattr(mh.CompletionHandler, "handle", _handle)
        monkeypatch.setattr(
            appmod, "extract_api_key",
            lambda auth, sb=False: sandbox_return if sb else "from-bearer",
        )

        client = TestClient(appmod.app)
        client.post(
            "/v1/messages",
            json={"model": "m", "max_tokens": 10, "messages": []},
            headers=headers,
        )
        return seen

    def test_x_api_key_is_accepted(self, monkeypatch):
        seen = self._call({"x-api-key": "sk-anthropic-style"}, monkeypatch)
        assert seen["api_key"] == "sk-anthropic-style"

    def test_bearer_still_works(self, monkeypatch):
        seen = self._call({"Authorization": "Bearer sk-1"}, monkeypatch)
        assert seen["api_key"] == "from-bearer"

    def test_sandbox_ignores_x_api_key(self, monkeypatch):
        """Sandbox verifies a JWT in the bearer header. x-api-key must not be
        a way around that."""
        seen = self._call(
            {"x-api-key": "sk-raw", "Authorization": "Bearer jwt",
             "x-sandbox": "true"},
            monkeypatch,
        )
        assert seen["api_key"] == "sandbox:org:user"
        assert seen["api_key"] != "sk-raw"


class TestUnsupportedContent:
    """Rejecting beats dropping: a request that loses its image still answers."""

    def test_an_image_block_is_rejected(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            surface.request_to_openai({
                "model": "m",
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "image", "source": {"type": "base64", "data": "..."}},
                ]}],
            })
        assert exc.value.status_code == 400
        assert "image" in exc.value.detail

    def test_the_message_names_every_unsupported_type(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            surface.request_to_openai({
                "model": "m",
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {}},
                    {"type": "tool_result", "content": "x"},
                ]}],
            })
        assert "image" in exc.value.detail
        assert "tool_result" in exc.value.detail

    def test_a_system_prompt_with_blocks_is_checked_too(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            surface.request_to_openai({
                "model": "m",
                "system": [{"type": "image", "source": {}}],
                "messages": [],
            })

    def test_text_only_blocks_still_pass(self):
        got = surface.request_to_openai({
            "model": "m",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "one "},
                {"type": "text", "text": "two"},
            ]}],
        })
        assert got["messages"][0]["content"] == "one two"


@pytest.mark.asyncio
async def test_concurrent_streams_do_not_interleave():
    """A surface translator is built per request, unlike a provider adapter.

    Two streams running at once must each keep their own message id, text and
    envelope. Nothing else in these tests runs more than one at a time, and
    this is the failure a live smoke test is least likely to catch.
    """
    import asyncio

    from inference.core.handlers.messages import MessagesHandler

    def _chunks(word, n):
        return [
            b"data: "
            + ('{"model":"m","choices":[{"delta":{"content":"%s%d"}}]}' % (word, i)).encode()
            + b"\n\n"
            for i in range(n)
        ]

    async def _run(word, n, delay):
        async def _src():
            for c in _chunks(word, n):
                await asyncio.sleep(delay)  # force the two to interleave
                yield c

        translator = surface.StreamTranslator(model="m")
        frames = []
        async for frame in MessagesHandler._translate_stream(_src(), translator):
            frames.append(frame)
        return b"".join(frames).decode("utf-8")

    first, second = await asyncio.gather(
        _run("alpha", 5, 0.001),
        _run("beta", 5, 0.001),
    )

    assert "beta" not in first
    assert "alpha" not in second

    def _ids(text):
        return {
            json.loads(f.split("data: ", 1)[1])["message"]["id"]
            for f in text.split("\n\n")
            if "message_start" in f and "data: " in f
        }

    assert len(_ids(first)) == 1
    assert _ids(first).isdisjoint(_ids(second)), "both streams share a message id"

    for text in (first, second):
        # The event name appears in the `event:` line and again in the body,
        # so count the line.
        assert text.count("event: message_start") == 1
        assert text.count("event: message_stop") == 1
