"""Tests that usage-tracking errors are routed through the logger, not print()."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.fixture
def mock_settings():
    """Provide minimal settings so ApiGatewayClient can be instantiated."""
    with patch("inferia.services.inference.client.settings") as s:
        s.api_gateway_url = "http://localhost:8000"
        s.api_gateway_internal_key = "test-key"
        s.request_timeout = 5
        s.context_cache_maxsize = 10
        s.context_cache_ttl = 30
        s.quota_check_cache_ttl_seconds = 1.0
        s.quota_check_cache_maxsize = 100
        s.gateway_http_max_connections = 100
        s.gateway_http_max_keepalive_connections = 10
        yield s


@pytest.fixture
def gateway_client(mock_settings):
    from inferia.services.inference.client import ApiGatewayClient

    return ApiGatewayClient()


@pytest.mark.asyncio
async def test_track_usage_logs_error_on_failure(gateway_client):
    """track_usage must call logger.error (not print) when the HTTP call fails."""
    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=RuntimeError("connection refused"))
    fake_client.is_closed = False
    gateway_client._client = fake_client

    with patch("inferia.services.inference.client.logger") as mock_logger:
        await gateway_client.track_usage(
            user_id="user-1",
            model="gpt-4",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        mock_logger.error.assert_called_once()
        args = mock_logger.error.call_args[0][0]
        assert "Failed to track usage" in args


@pytest.mark.asyncio
async def test_track_usage_does_not_print_on_failure(gateway_client):
    """track_usage must NOT use print() for error reporting."""
    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=RuntimeError("connection refused"))
    fake_client.is_closed = False
    gateway_client._client = fake_client

    with patch("builtins.print") as mock_print:
        await gateway_client.track_usage(
            user_id="user-1",
            model="gpt-4",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        mock_print.assert_not_called()
