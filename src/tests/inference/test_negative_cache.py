"""Tests for negative cache on gateway connection failures."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException


@pytest.fixture
def gateway_client():
    """Create a fresh ApiGatewayClient with mocked settings."""
    with patch("inference.client.settings") as mock_settings:
        mock_settings.api_gateway_url = "http://gateway:8000"
        mock_settings.api_gateway_internal_key = "test-key"
        mock_settings.request_timeout = 5.0
        mock_settings.context_cache_maxsize = 100
        mock_settings.context_cache_ttl = 60
        mock_settings.quota_check_cache_ttl_seconds = 5.0
        mock_settings.quota_check_cache_maxsize = 100
        mock_settings.gateway_http_max_connections = 100
        mock_settings.gateway_http_max_keepalive_connections = 10

        from inference.client import ApiGatewayClient

        client = ApiGatewayClient()
        yield client


class TestNegativeCacheResolveContext:
    """Negative cache prevents thundering herd on resolve_context failures."""

    @pytest.mark.asyncio
    async def test_first_connection_failure_attempts_request(self, gateway_client):
        """First call should attempt the connection and fail with 503."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.is_closed = False
        gateway_client._client = mock_client

        with pytest.raises(HTTPException) as exc:
            await gateway_client.resolve_context("key-1", "model-1")
        assert exc.value.status_code == 500  # existing behavior from bare Exception handler

        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_call_within_ttl_skips_connection(self, gateway_client):
        """Second call within TTL should fail immediately WITHOUT attempting connection."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.is_closed = False
        gateway_client._client = mock_client

        # First call - attempts connection
        with pytest.raises(HTTPException):
            await gateway_client.resolve_context("key-1", "model-1")
        assert mock_client.post.call_count == 1

        # Second call - should NOT attempt connection (negative cache hit)
        with pytest.raises(HTTPException) as exc:
            await gateway_client.resolve_context("key-1", "model-1")
        assert exc.value.status_code == 503
        assert "temporarily unavailable" in exc.value.detail.lower()

        # post should still only have been called once
        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_after_ttl_expires_retries_connection(self, gateway_client):
        """After TTL expires, should retry the connection."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.is_closed = False
        gateway_client._client = mock_client

        # First call - attempts connection
        with pytest.raises(HTTPException):
            await gateway_client.resolve_context("key-1", "model-1")
        assert mock_client.post.call_count == 1

        # Expire the negative cache by backdating the entry
        for key in list(gateway_client._negative_cache):
            gateway_client._negative_cache[key] = time.monotonic() - 10.0

        # Third call - TTL expired, should retry
        with pytest.raises(HTTPException):
            await gateway_client.resolve_context("key-1", "model-1")
        assert mock_client.post.call_count == 2


class TestNegativeCacheCheckQuota:
    """Negative cache prevents thundering herd on check_quota failures."""

    @pytest.mark.asyncio
    async def test_first_quota_failure_attempts_request(self, gateway_client):
        """First quota check should attempt the connection."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.is_closed = False
        gateway_client._client = mock_client

        with pytest.raises(HTTPException):
            await gateway_client.check_quota("user-1", "model-1")
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_quota_call_within_ttl_skips_connection(self, gateway_client):
        """Second quota call within TTL should fail without attempting connection."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.is_closed = False
        gateway_client._client = mock_client

        # First call
        with pytest.raises(HTTPException):
            await gateway_client.check_quota("user-1", "model-1")
        assert mock_client.post.call_count == 1

        # Second call - negative cache hit
        with pytest.raises(HTTPException) as exc:
            await gateway_client.check_quota("user-1", "model-1")
        assert exc.value.status_code == 503

        # post still called only once
        assert mock_client.post.call_count == 1


class TestNegativeCacheDoesNotAffectHttpErrors:
    """HTTP errors (429, 500) should NOT be negatively cached."""

    @pytest.mark.asyncio
    async def test_http_429_not_cached(self, gateway_client):
        """429 rate-limit errors should propagate normally, not cached."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.json.return_value = {"detail": "Quota exceeded"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Rate limited", request=MagicMock(), response=mock_response
            )
        )
        mock_client.is_closed = False
        gateway_client._client = mock_client

        with pytest.raises(HTTPException) as exc:
            await gateway_client.check_quota("user-1", "model-1")
        assert exc.value.status_code == 429

        # Negative cache should be empty - HTTP errors are not cached
        assert len(gateway_client._negative_cache) == 0

    @pytest.mark.asyncio
    async def test_http_500_not_cached(self, gateway_client):
        """500 server errors should propagate normally, not cached."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"detail": "Internal error"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Server error", request=MagicMock(), response=mock_response
            )
        )
        mock_client.is_closed = False
        gateway_client._client = mock_client

        with pytest.raises(HTTPException) as exc:
            await gateway_client.check_quota("user-1", "model-1")
        assert exc.value.status_code == 500

        # Negative cache should be empty
        assert len(gateway_client._negative_cache) == 0
