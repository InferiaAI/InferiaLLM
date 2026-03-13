"""
Tests for provider error sanitization in the Inference service.

Verifies that:
1. Upstream provider error bodies are NOT forwarded to API callers
2. Generic error messages are returned instead
3. Full error details are still logged server-side
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from inferia.services.inference.core.service import GatewayService


class TestCallUpstreamErrorSanitization:
    """Verify call_upstream does not leak provider error details."""

    @pytest.mark.asyncio
    async def test_http_error_does_not_expose_response_body(self):
        """Provider HTTP error body must not be forwarded to the caller."""
        # Create a mock response with sensitive error body
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = (
            "Internal error: account sk-abc123 exceeded quota on "
            "endpoint https://internal.provider.com/v1/completions"
        )

        error = httpx.HTTPStatusError(
            "Server error", request=MagicMock(), response=mock_response
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=error)

        with patch(
            "inferia.services.inference.core.service.http_client.get_client",
            return_value=mock_client,
        ), patch(
            "inferia.services.inference.core.service.upstream_concurrency_limiter.limit",
            return_value=AsyncMock().__aenter__(),
        ):
            # Use a context manager mock for the concurrency limiter
            limiter_cm = MagicMock()
            limiter_cm.__aenter__ = AsyncMock(return_value=None)
            limiter_cm.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "inferia.services.inference.core.service.upstream_concurrency_limiter.limit",
                return_value=limiter_cm,
            ):
                from fastapi import HTTPException

                with pytest.raises(HTTPException) as exc_info:
                    await GatewayService.call_upstream(
                        endpoint_url="http://provider:8000",
                        payload={"model": "test", "messages": []},
                        headers={},
                        engine="vllm",
                    )

                # The detail must NOT contain the sensitive provider text
                assert "sk-abc123" not in str(exc_info.value.detail)
                assert "internal.provider.com" not in str(exc_info.value.detail)
                assert exc_info.value.detail == "Upstream provider returned an error"
                assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_generic_exception_does_not_expose_details(self):
        """Connection errors must not leak internal details."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=ConnectionError(
                "Failed to connect to internal-gpu-cluster.vpc.local:8080"
            )
        )

        limiter_cm = MagicMock()
        limiter_cm.__aenter__ = AsyncMock(return_value=None)
        limiter_cm.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "inferia.services.inference.core.service.http_client.get_client",
            return_value=mock_client,
        ), patch(
            "inferia.services.inference.core.service.upstream_concurrency_limiter.limit",
            return_value=limiter_cm,
        ):
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await GatewayService.call_upstream(
                    endpoint_url="http://provider:8000",
                    payload={"model": "test", "messages": []},
                    headers={},
                    engine="vllm",
                )

            assert "internal-gpu-cluster" not in str(exc_info.value.detail)
            assert "vpc.local" not in str(exc_info.value.detail)
            assert exc_info.value.detail == "Upstream provider is unavailable"
            assert exc_info.value.status_code == 502


class TestStreamUpstreamErrorSanitization:
    """Verify stream_upstream does not leak provider error details."""

    @pytest.mark.asyncio
    async def test_streaming_http_error_does_not_expose_body(self):
        """Streaming HTTP errors must return generic message."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limit exceeded for org-secret-id-12345"

        error = httpx.HTTPStatusError(
            "Rate limited", request=MagicMock(), response=mock_response
        )

        mock_client = AsyncMock()
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(side_effect=error)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(return_value=stream_cm)

        limiter_cm = MagicMock()
        limiter_cm.__aenter__ = AsyncMock(return_value=None)
        limiter_cm.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "inferia.services.inference.core.service.http_client.get_client",
            return_value=mock_client,
        ), patch(
            "inferia.services.inference.core.service.upstream_concurrency_limiter.limit",
            return_value=limiter_cm,
        ):
            chunks = []
            async for chunk in GatewayService.stream_upstream(
                endpoint_url="http://provider:8000",
                payload={"model": "test", "messages": []},
                headers={},
            ):
                chunks.append(chunk)

            combined = b"".join(chunks).decode()
            assert "org-secret-id-12345" not in combined
            assert "Rate limit exceeded" not in combined
            assert "Upstream provider returned an error" in combined

    @pytest.mark.asyncio
    async def test_streaming_exception_does_not_expose_details(self):
        """Streaming connection errors must return generic message."""
        mock_client = AsyncMock()
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(
            side_effect=ConnectionError("DNS resolution failed for gpu-node-3.internal")
        )
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(return_value=stream_cm)

        limiter_cm = MagicMock()
        limiter_cm.__aenter__ = AsyncMock(return_value=None)
        limiter_cm.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "inferia.services.inference.core.service.http_client.get_client",
            return_value=mock_client,
        ), patch(
            "inferia.services.inference.core.service.upstream_concurrency_limiter.limit",
            return_value=limiter_cm,
        ):
            chunks = []
            async for chunk in GatewayService.stream_upstream(
                endpoint_url="http://provider:8000",
                payload={"model": "test", "messages": []},
                headers={},
            ):
                chunks.append(chunk)

            combined = b"".join(chunks).decode()
            assert "gpu-node-3.internal" not in combined
            assert "DNS resolution" not in combined
            assert "Streaming connection failed" in combined


class TestErrorsStillLogged:
    """Verify full error details are logged even though they're not returned."""

    @pytest.mark.asyncio
    async def test_http_error_logs_full_details(self):
        """Full provider error body must be logged for debugging."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Secret provider error details"

        error = httpx.HTTPStatusError(
            "Server error", request=MagicMock(), response=mock_response
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=error)

        limiter_cm = MagicMock()
        limiter_cm.__aenter__ = AsyncMock(return_value=None)
        limiter_cm.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "inferia.services.inference.core.service.http_client.get_client",
            return_value=mock_client,
        ), patch(
            "inferia.services.inference.core.service.upstream_concurrency_limiter.limit",
            return_value=limiter_cm,
        ), patch(
            "inferia.services.inference.core.service.logger"
        ) as mock_logger:
            from fastapi import HTTPException

            with pytest.raises(HTTPException):
                await GatewayService.call_upstream(
                    endpoint_url="http://provider:8000",
                    payload={"model": "test", "messages": []},
                    headers={},
                    engine="vllm",
                )

            # Logger should have received the full error body
            mock_logger.error.assert_called_once()
            log_message = mock_logger.error.call_args[0][0]
            assert "Secret provider error details" in log_message
