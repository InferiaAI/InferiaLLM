"""Tests for inference service routing logic — Layer 3."""

import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx


@asynccontextmanager
async def noop_limit(key):
    yield


def make_mock_adapter():
    adapter = MagicMock()
    adapter.get_chat_path.return_value = "/v1/chat/completions"
    adapter.transform_request.side_effect = lambda x: x
    adapter.transform_response.side_effect = lambda x: x
    return adapter


class TestBuildFullUrl:
    """URL construction logic."""

    def test_no_duplicate_v1_prefix(self):
        from inferia.services.inference.core.service import GatewayService

        url = GatewayService._build_full_url(
            "http://host:8000/v1", "/v1/chat/completions"
        )
        assert url == "http://host:8000/v1/chat/completions"
        assert "v1/v1" not in url

    def test_no_v1_on_base_appends_correctly(self):
        from inferia.services.inference.core.service import GatewayService

        url = GatewayService._build_full_url(
            "http://host:8000", "/v1/chat/completions"
        )
        assert url == "http://host:8000/v1/chat/completions"

    def test_trailing_slash_on_base_handled(self):
        from inferia.services.inference.core.service import GatewayService

        url = GatewayService._build_full_url(
            "http://host:8000/", "/v1/chat/completions"
        )
        assert not url.startswith("http://host:8000//")


class TestCallUpstreamRouting:
    """call_upstream selects adapter and forwards request."""

    @pytest.mark.asyncio
    async def test_successful_response_returned(self):
        """Successful upstream call returns parsed JSON."""
        from inferia.services.inference.core.service import GatewayService

        response_data = {"choices": [{"message": {"content": "Hello!"}}]}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = response_data
        mock_resp.headers = {}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch(
            "inferia.services.inference.core.service.get_adapter",
            return_value=make_mock_adapter(),
        ), patch(
            "inferia.services.inference.core.service.http_client"
        ) as mock_hc, patch(
            "inferia.services.inference.core.service.upstream_concurrency_limiter"
        ) as mock_limiter:
            mock_hc.get_client.return_value = mock_client
            mock_limiter.limit = noop_limit

            result = await GatewayService.call_upstream(
                "http://provider:8000", {"model": "test"}, {}
            )
            assert result["choices"][0]["message"]["content"] == "Hello!"

    @pytest.mark.asyncio
    async def test_provider_500_raises_http_exception(self):
        """Provider 500 raises HTTPException."""
        from inferia.services.inference.core.service import GatewayService
        from fastapi import HTTPException

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Error", request=MagicMock(), response=mock_resp
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch(
            "inferia.services.inference.core.service.get_adapter",
            return_value=make_mock_adapter(),
        ), patch(
            "inferia.services.inference.core.service.http_client"
        ) as mock_hc, patch(
            "inferia.services.inference.core.service.upstream_concurrency_limiter"
        ) as mock_limiter:
            mock_hc.get_client.return_value = mock_client
            mock_limiter.limit = noop_limit

            with pytest.raises(HTTPException) as exc:
                await GatewayService.call_upstream(
                    "http://provider:8000", {"model": "test"}, {}
                )
            assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_connection_refused_returns_502(self):
        """Provider connection refused returns 502."""
        from inferia.services.inference.core.service import GatewayService
        from fastapi import HTTPException

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        with patch(
            "inferia.services.inference.core.service.get_adapter",
            return_value=make_mock_adapter(),
        ), patch(
            "inferia.services.inference.core.service.http_client"
        ) as mock_hc, patch(
            "inferia.services.inference.core.service.upstream_concurrency_limiter"
        ) as mock_limiter:
            mock_hc.get_client.return_value = mock_client
            mock_limiter.limit = noop_limit

            with pytest.raises(HTTPException) as exc:
                await GatewayService.call_upstream(
                    "http://provider:8000", {"model": "test"}, {}
                )
            assert exc.value.status_code == 502
