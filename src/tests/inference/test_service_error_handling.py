"""Tests for inference service error handling."""

import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException


@asynccontextmanager
async def noop_limit(key):
    yield


def make_mock_adapter():
    adapter = MagicMock()
    adapter.get_chat_path.return_value = "/v1/chat/completions"
    adapter.transform_request.side_effect = lambda x: x
    adapter.transform_response.side_effect = lambda x: x
    return adapter


class TestGatewayServiceErrors:
    """Verify inference gateway error handling."""

    @pytest.mark.asyncio
    async def test_call_upstream_http_error_proxies_status(self):
        """HTTPStatusError from provider proxies status code."""
        from services.inference.core.service import GatewayService

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Error", request=MagicMock(), response=mock_resp
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch(
            "services.inference.core.service.get_adapter",
            return_value=make_mock_adapter(),
        ), patch(
            "services.inference.core.service.http_client"
        ) as mock_hc, patch(
            "services.inference.core.service.upstream_concurrency_limiter"
        ) as mock_limiter:
            mock_hc.get_client.return_value = mock_client
            mock_limiter.limit = noop_limit

            with pytest.raises(HTTPException) as exc:
                await GatewayService.call_upstream(
                    "http://provider:8000", {"model": "test"}, {}
                )
            assert exc.value.status_code == 500
            assert "Upstream provider" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_call_upstream_connection_error_returns_502(self):
        """Connection failure returns 502."""
        from services.inference.core.service import GatewayService

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        with patch(
            "services.inference.core.service.get_adapter",
            return_value=make_mock_adapter(),
        ), patch(
            "services.inference.core.service.http_client"
        ) as mock_hc, patch(
            "services.inference.core.service.upstream_concurrency_limiter"
        ) as mock_limiter:
            mock_hc.get_client.return_value = mock_client
            mock_limiter.limit = noop_limit

            with pytest.raises(HTTPException) as exc:
                await GatewayService.call_upstream(
                    "http://provider:8000", {"model": "test"}, {}
                )
            assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_resolve_context_invalid_returns_401(self):
        """Invalid context raises 401."""
        from services.inference.core.service import GatewayService

        with patch(
            "services.inference.core.service.api_gateway_client"
        ) as mock_client:
            mock_client.resolve_context = AsyncMock(
                return_value={"valid": False, "error": "Invalid API Key"}
            )
            with pytest.raises(HTTPException) as exc:
                await GatewayService.resolve_context("bad-key", "model")
            assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_build_full_url_prevents_duplicate_v1(self):
        """URL builder prevents duplicate /v1/v1 paths."""
        from services.inference.core.service import GatewayService

        assert (
            GatewayService._build_full_url(
                "http://host:8000/v1", "/v1/chat/completions"
            )
            == "http://host:8000/v1/chat/completions"
        )
        assert (
            GatewayService._build_full_url(
                "http://host:8000", "/v1/chat/completions"
            )
            == "http://host:8000/v1/chat/completions"
        )
