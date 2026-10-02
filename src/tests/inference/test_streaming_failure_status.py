"""A failed streaming request must not be logged as a successful one.

`stream_upstream` cannot raise past its consumer without ending the stream, so
it reports upstream failures as data frames. The wrapper's `except` clauses
therefore never fire, and the only detector was a substring check for
`Upstream Error` — text that appears in a `logger.error` line, not in any frame
the generator emits. It never matched, so circuit-breaker trips, size-limit
aborts and provider 500s were all recorded as `status_code=200`, in the
inference log and in the Prometheus counters alike.

The failure now travels out of band in a sink dict, which cannot drift when
someone rewords a message.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inference.core import service as svc
from inference.core.service import GatewayService, _upstream_error


class TestTheHelper:

    def test_the_frame_is_what_the_client_already_received(self):
        """The payload is unchanged; only the reporting is new."""
        got = _upstream_error(None, 502, "Upstream provider returned an error")
        assert got == b'data: {"error": "Upstream provider returned an error"}\n\n'

    def test_the_sink_records_the_failure(self):
        sink = {}
        _upstream_error(sink, 503, "Upstream temporarily unavailable")
        assert sink == {
            "status_code": 503,
            "message": "Upstream temporarily unavailable",
        }

    def test_no_sink_is_not_an_error(self):
        """The generator is usable without a caller that wants to know."""
        assert _upstream_error(None, 502, "boom").startswith(b"data: ")

    def test_the_frame_stays_valid_json(self):
        frame = _upstream_error({}, 502, 'a "quoted" message')
        payload = json.loads(frame.decode()[len("data: "):].strip())
        assert payload == {"error": 'a "quoted" message'}


async def _drain(gen):
    return b"".join([chunk async for chunk in gen])


@pytest.mark.asyncio
class TestEachFailurePathReportsItself:

    async def test_invalid_upstream_configuration(self):
        sink = {}
        with patch.object(
            svc, "validate_upstream_url", side_effect=ValueError("bad host")
        ):
            out = await _drain(GatewayService.stream_upstream(
                "http://up.test", {}, {}, "vllm", error_sink=sink,
            ))
        assert sink["status_code"] == 500
        assert b"Invalid upstream configuration" in out

    async def test_circuit_breaker_open(self):
        sink = {}
        breaker = MagicMock()
        breaker._can_execute = AsyncMock(return_value=False)
        with patch.object(
            svc.circuit_breaker_registry, "get_or_create", return_value=breaker
        ):
            out = await _drain(GatewayService.stream_upstream(
                "http://up.test", {}, {}, "vllm", error_sink=sink,
            ))
        assert sink["status_code"] == 503
        assert b"circuit breaker open" in out

    async def test_upstream_returned_an_error(self):
        import httpx

        sink = {}
        breaker = MagicMock()
        breaker._can_execute = AsyncMock(return_value=True)
        breaker._record_failure = AsyncMock()

        response = MagicMock(status_code=500, text="boom")
        client = MagicMock()
        client.build_request.return_value = MagicMock()
        client.send = AsyncMock(
            side_effect=httpx.HTTPStatusError("x", request=MagicMock(), response=response)
        )

        with patch.object(svc.circuit_breaker_registry, "get_or_create", return_value=breaker), \
             patch.object(svc.http_client, "get_client", return_value=client):
            out = await _drain(GatewayService.stream_upstream(
                "http://up.test", {}, {}, "vllm", error_sink=sink,
            ))

        assert sink["status_code"] == 502
        assert b"Upstream provider returned an error" in out

    async def test_connection_failed(self):
        sink = {}
        breaker = MagicMock()
        breaker._can_execute = AsyncMock(return_value=True)
        breaker._record_failure = AsyncMock()

        client = MagicMock()
        client.build_request.return_value = MagicMock()
        client.send = AsyncMock(side_effect=RuntimeError("socket died"))

        with patch.object(svc.circuit_breaker_registry, "get_or_create", return_value=breaker), \
             patch.object(svc.http_client, "get_client", return_value=client):
            out = await _drain(GatewayService.stream_upstream(
                "http://up.test", {}, {}, "vllm", error_sink=sink,
            ))

        assert sink["status_code"] == 502
        assert b"Streaming connection failed" in out

    async def test_response_exceeded_size_limit(self):
        """Named in the issue alongside breaker trips and provider 500s.

        The abort happens mid-stream, after chunks have already reached the
        client, so it is the one failure the caller is most likely to miss.
        """
        sink = {}
        breaker = MagicMock()
        breaker._can_execute = AsyncMock(return_value=True)

        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.aclose = AsyncMock()

        async def _raw():
            yield b"x" * 100
            yield b"x" * 100
        response.aiter_raw = _raw

        client = MagicMock()
        client.build_request.return_value = MagicMock()
        client.send = AsyncMock(return_value=response)

        with patch.object(svc.circuit_breaker_registry, "get_or_create", return_value=breaker),              patch.object(svc.http_client, "get_client", return_value=client),              patch.object(svc.settings, "upstream_max_response_bytes", 150):
            out = await _drain(GatewayService.stream_upstream(
                "http://up.test", {}, {}, "vllm", error_sink=sink,
            ))

        assert sink["status_code"] == 502
        assert sink["message"] == "Upstream response exceeded size limit"
        assert b"exceeded size limit" in out

    async def test_a_stream_that_works_records_nothing(self):
        """An empty sink is what tells the caller the request succeeded."""
        sink = {}
        breaker = MagicMock()
        breaker._can_execute = AsyncMock(return_value=True)

        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.aclose = AsyncMock()

        async def _raw():
            yield b"data: {}\n\n"
        response.aiter_raw = _raw

        client = MagicMock()
        client.build_request.return_value = MagicMock()
        client.send = AsyncMock(return_value=response)

        with patch.object(svc.circuit_breaker_registry, "get_or_create", return_value=breaker), \
             patch.object(svc.http_client, "get_client", return_value=client):
            await _drain(GatewayService.stream_upstream(
                "http://up.test", {}, {}, "vllm", error_sink=sink,
            ))

        assert sink == {}


def test_no_failure_text_is_matched_by_substring():
    """The old detector looked for `Upstream Error`, which only ever appeared
    in a log line. Nothing should depend on a message's wording again."""
    import inspect

    source = inspect.getsource(svc.GatewayService.stream_upstream)
    assert "Upstream Error" not in source.replace(
        'logger.error(f"Upstream Error', ""
    ), "a failure message is being matched by its text again"


@pytest.mark.asyncio
class TestWhatActuallyReachesTheLog:
    """The point of the issue: what RequestLogger is told."""

    @staticmethod
    async def _run(sink_contents):
        import asyncio

        from inference.core.handlers import completion as ch
        from inference.core.providers.external.openai import OpenAIAdapter

        logged = {}

        async def _log(**kwargs):
            logged.update(kwargs)

        def _stream(*_a, error_sink=None, **_k):
            async def _gen():
                if sink_contents and error_sink is not None:
                    error_sink.update(sink_contents)
                yield b'data: {"error": "something"}\n\n' if sink_contents else b"data: {}\n\n"
            return _gen()

        with patch.object(ch.RequestLogger, "log", _log), \
             patch.object(ch.GatewayService, "stream_upstream", _stream):
            response = ch.CompletionHandler._handle_streaming(
                "http://up.test", {}, {}, "vllm", OpenAIAdapter(),
                "dep-1", "user-1", "m", {}, 0.0, MagicMock(),
                [], False, "10.0.0.1", "key",
            )
            async for _ in response.body_iterator:
                pass
            # the log is scheduled as a task, so let it run
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        return logged

    async def test_an_upstream_failure_is_logged_as_a_failure(self):
        logged = await self._run(
            {"status_code": 502, "message": "Upstream provider returned an error"}
        )
        assert logged["status_code"] == 502
        assert logged["error_message"] == "Upstream provider returned an error"

    async def test_the_circuit_breaker_status_survives(self):
        """Not flattened to a generic 502: 503 says the breaker is open."""
        logged = await self._run(
            {"status_code": 503, "message": "Upstream temporarily unavailable"}
        )
        assert logged["status_code"] == 503

    async def test_a_working_stream_is_still_logged_as_success(self):
        logged = await self._run(None)
        assert logged["status_code"] == 200
        assert logged["error_message"] is None
