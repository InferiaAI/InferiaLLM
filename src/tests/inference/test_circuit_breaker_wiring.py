"""Tests for circuit breaker wiring into upstream call/stream paths."""

import time
import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException

from common.circuit_breaker import circuit_breaker_registry


@asynccontextmanager
async def noop_limit(key):
    yield


def make_mock_adapter():
    adapter = MagicMock()
    adapter.get_chat_path.return_value = "/v1/chat/completions"
    adapter.transform_request.side_effect = lambda x: x
    adapter.transform_response.side_effect = lambda x: x
    return adapter


def make_failing_client(status_code=500):
    """Create a mock client whose .post() raises HTTPStatusError."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = "Internal Server Error"
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Error", request=MagicMock(), response=mock_resp
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


def make_successful_client(response_body=None):
    """Create a mock client whose .post() returns a valid response."""
    if response_body is None:
        response_body = {"choices": [{"message": {"content": "ok"}}]}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.content = b'{"choices":[]}'
    mock_resp.json.return_value = response_body
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


def _patch_service(mock_client):
    """Return stacked patches for call_upstream dependencies."""
    return (
        patch(
            "inference.core.service.get_adapter",
            return_value=make_mock_adapter(),
        ),
        patch("inference.core.service.http_client"),
        patch("inference.core.service.upstream_concurrency_limiter"),
        patch("inference.core.service.settings"),
        mock_client,
    )


class TestCircuitBreakerWiring:
    """Verify circuit breaker is wired into call_upstream."""

    @pytest.mark.asyncio
    async def test_call_upstream_opens_circuit_after_failures(self):
        """After 5 consecutive upstream failures, the 6th call raises 503 (circuit open)."""
        from inference.core.service import GatewayService

        concurrency_key = "deploy-fail-test"

        with patch(
            "inference.core.service.get_adapter",
            return_value=make_mock_adapter(),
        ), patch(
            "inference.core.service.http_client"
        ) as mock_hc, patch(
            "inference.core.service.upstream_concurrency_limiter"
        ) as mock_limiter, patch(
            "inference.core.service.settings"
        ) as mock_settings:
            mock_hc.get_client.return_value = make_failing_client()
            mock_limiter.limit = noop_limit
            mock_settings.upstream_allowed_internal_hosts = ""
            mock_settings.upstream_max_response_bytes = 52_428_800

            # Trigger 5 failures to open the circuit
            for _ in range(5):
                with pytest.raises(HTTPException) as exc:
                    await GatewayService.call_upstream(
                        "https://api.example.com",
                        {"model": "test"},
                        {},
                        concurrency_key=concurrency_key,
                    )
                # These should be upstream errors (500), not circuit breaker
                assert exc.value.status_code == 500

            # 6th call should be blocked by the circuit breaker (503)
            with pytest.raises(HTTPException) as exc:
                await GatewayService.call_upstream(
                    "https://api.example.com",
                    {"model": "test"},
                    {},
                    concurrency_key=concurrency_key,
                )
            assert exc.value.status_code == 503
            assert "circuit breaker" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_call_upstream_circuit_recovers_after_timeout(self):
        """After recovery timeout, a successful call closes the circuit."""
        from inference.core.service import GatewayService

        concurrency_key = "deploy-recover-test"

        with patch(
            "inference.core.service.get_adapter",
            return_value=make_mock_adapter(),
        ), patch(
            "inference.core.service.http_client"
        ) as mock_hc, patch(
            "inference.core.service.upstream_concurrency_limiter"
        ) as mock_limiter, patch(
            "inference.core.service.settings"
        ) as mock_settings:
            mock_hc.get_client.return_value = make_failing_client()
            mock_limiter.limit = noop_limit
            mock_settings.upstream_allowed_internal_hosts = ""
            mock_settings.upstream_max_response_bytes = 52_428_800

            # Trigger 5 failures to open the circuit
            for _ in range(5):
                with pytest.raises(HTTPException):
                    await GatewayService.call_upstream(
                        "https://api.example.com",
                        {"model": "test"},
                        {},
                        concurrency_key=concurrency_key,
                    )

            # Simulate recovery timeout by backdating last_failure_time
            breaker = circuit_breaker_registry.get(f"upstream:{concurrency_key}")
            assert breaker is not None
            breaker._last_failure_time = time.time() - 60  # Well past recovery_timeout

            # Now switch to a successful client
            mock_hc.get_client.return_value = make_successful_client()

            # This call should succeed (half-open -> closed)
            result = await GatewayService.call_upstream(
                "https://api.example.com",
                {"model": "test"},
                {},
                concurrency_key=concurrency_key,
            )
            assert result is not None

            # Circuit should now be closed
            from common.circuit_breaker import CircuitState

            assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_call_upstream_success_does_not_open_circuit(self):
        """Successful calls keep the circuit closed."""
        from inference.core.service import GatewayService

        concurrency_key = "deploy-success-test"

        with patch(
            "inference.core.service.get_adapter",
            return_value=make_mock_adapter(),
        ), patch(
            "inference.core.service.http_client"
        ) as mock_hc, patch(
            "inference.core.service.upstream_concurrency_limiter"
        ) as mock_limiter, patch(
            "inference.core.service.settings"
        ) as mock_settings:
            mock_hc.get_client.return_value = make_successful_client()
            mock_limiter.limit = noop_limit
            mock_settings.upstream_allowed_internal_hosts = ""
            mock_settings.upstream_max_response_bytes = 52_428_800

            for _ in range(10):
                result = await GatewayService.call_upstream(
                    "https://api.example.com",
                    {"model": "test"},
                    {},
                    concurrency_key=concurrency_key,
                )
                assert result is not None

            # Circuit should remain closed
            breaker = circuit_breaker_registry.get(f"upstream:{concurrency_key}")
            from common.circuit_breaker import CircuitState

            assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_stream_upstream_records_failure_on_http_error(self):
        """stream_upstream records circuit breaker failure on HTTPStatusError."""
        from inference.core.service import GatewayService

        concurrency_key = "deploy-stream-fail"

        mock_response = AsyncMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Error", request=MagicMock(), response=MagicMock(status_code=500, text="err")
        )

        @asynccontextmanager
        async def mock_stream(*args, **kwargs):
            yield mock_response

        mock_client = MagicMock()
        mock_client.stream = mock_stream

        with patch(
            "inference.core.service.get_adapter",
            return_value=make_mock_adapter(),
        ), patch(
            "inference.core.service.http_client"
        ) as mock_hc, patch(
            "inference.core.service.upstream_concurrency_limiter"
        ) as mock_limiter, patch(
            "inference.core.service.settings"
        ) as mock_settings:
            mock_hc.get_client.return_value = mock_client
            mock_limiter.limit = noop_limit
            mock_settings.upstream_allowed_internal_hosts = ""
            mock_settings.upstream_max_response_bytes = 52_428_800

            # Trigger 5 streaming failures
            for _ in range(5):
                chunks = []
                async for chunk in GatewayService.stream_upstream(
                    "https://api.example.com",
                    {"model": "test"},
                    {},
                    concurrency_key=concurrency_key,
                ):
                    chunks.append(chunk)

            # 6th call should be blocked by circuit breaker
            chunks = []
            async for chunk in GatewayService.stream_upstream(
                "https://api.example.com",
                {"model": "test"},
                {},
                concurrency_key=concurrency_key,
            ):
                chunks.append(chunk)

            assert len(chunks) == 1
            assert b"circuit breaker" in chunks[0].lower()
