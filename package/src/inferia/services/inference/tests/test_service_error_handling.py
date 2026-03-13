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
        from inferia.services.inference.core.service import GatewayService

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
            assert "Upstream provider" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_call_upstream_connection_error_returns_502(self):
        """Connection failure returns 502."""
        from inferia.services.inference.core.service import GatewayService

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

    @pytest.mark.asyncio
    async def test_resolve_context_invalid_returns_401(self):
        """Invalid context raises 401."""
        from inferia.services.inference.core.service import GatewayService

        with patch(
            "inferia.services.inference.core.service.api_gateway_client"
        ) as mock_client:
            mock_client.resolve_context = AsyncMock(
                return_value={"valid": False, "error": "Invalid API Key"}
            )
            with pytest.raises(HTTPException) as exc:
                await GatewayService.resolve_context("bad-key", "model")
            assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_scan_input_violation_raises_400(self):
        """Guardrail violation raises 400."""
        from inferia.services.inference.core.service import GatewayService

        with patch(
            "inferia.services.inference.core.service.api_gateway_client"
        ) as mock_client:
            mock_client.scan_content = AsyncMock(
                return_value={
                    "is_valid": False,
                    "violations": [
                        {
                            "type": "toxicity",
                            "scanner": "Toxicity",
                            "score": 0.95,
                            "details": "toxic",
                        }
                    ],
                }
            )
            messages = [{"role": "user", "content": "bad text"}]
            with pytest.raises(HTTPException) as exc:
                await GatewayService.scan_input(messages, {"enabled": True}, "user-1")
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_scan_input_disabled_skips(self):
        """Disabled guardrails skip scanning."""
        from inferia.services.inference.core.service import GatewayService

        with patch(
            "inferia.services.inference.core.service.api_gateway_client"
        ) as mock_client:
            result = await GatewayService.scan_input(
                [{"role": "user", "content": "test"}],
                {"enabled": False},
                "user-1",
            )
            mock_client.scan_content.assert_not_called()
            assert result is None

    @pytest.mark.asyncio
    async def test_prompt_processing_error_fails_closed(self):
        """Prompt processing failure raises 500 (fail-closed)."""
        from inferia.services.inference.core.service import GatewayService

        with patch(
            "inferia.services.inference.core.service.api_gateway_client"
        ) as mock_client:
            mock_client.process_prompt = AsyncMock(
                side_effect=Exception("connection error")
            )

            with pytest.raises(HTTPException) as exc:
                await GatewayService.process_prompt(
                    [{"role": "user", "content": "test"}],
                    "model-1",
                    "user-1",
                    "org-1",
                    {"enabled": True},
                    {"enabled": True},
                    {},
                )
            assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_build_full_url_prevents_duplicate_v1(self):
        """URL builder prevents duplicate /v1/v1 paths."""
        from inferia.services.inference.core.service import GatewayService

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
