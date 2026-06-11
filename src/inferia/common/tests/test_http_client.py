"""Tests for InternalHttpClient — error handling layer."""

import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

from inferia.common.http_client import InternalHttpClient, request_id_ctx


class TestClientLifecycle:
    """Client creation and lifecycle."""

    def test_client_created_lazily(self):
        http = InternalHttpClient(internal_api_key="test-key")
        assert http._client is None
        # Accessing .client property creates it
        client = http.client
        assert client is not None
        assert isinstance(client, httpx.AsyncClient)

    def test_reuses_same_client(self):
        http = InternalHttpClient(internal_api_key="test-key")
        c1 = http.client
        c2 = http.client
        assert c1 is c2

    @pytest.mark.asyncio
    async def test_close_marks_client_none(self):
        http = InternalHttpClient(internal_api_key="test-key")
        _ = http.client  # create it
        await http.close()
        assert http._client is None

    @pytest.mark.asyncio
    async def test_new_client_after_close(self):
        http = InternalHttpClient(internal_api_key="test-key")
        c1 = http.client
        await http.close()
        c2 = http.client
        assert c1 is not c2


class TestDefaultHeaders:
    """Header injection."""

    def test_api_key_in_headers(self):
        http = InternalHttpClient(internal_api_key="my-secret-key")
        headers = http.get_default_headers()
        assert headers["X-Internal-API-Key"] == "my-secret-key"

    def test_request_id_included_when_set(self):
        http = InternalHttpClient(internal_api_key="key")
        token = request_id_ctx.set("req-12345")
        try:
            headers = http.get_default_headers()
            assert headers["X-Request-ID"] == "req-12345"
        finally:
            request_id_ctx.reset(token)

    def test_request_id_absent_when_not_set(self):
        http = InternalHttpClient(internal_api_key="key")
        # Ensure context is clean
        assert request_id_ctx.get() is None
        headers = http.get_default_headers()
        assert "X-Request-ID" not in headers


class TestErrorHandling:
    """HTTP errors logged without leaking keys."""

    @pytest.mark.asyncio
    async def test_timeout_raises_exception(self):
        http = InternalHttpClient(
            internal_api_key="key",
            base_url="http://localhost:1",  # non-routable
            timeout_seconds=0.1,
        )
        with pytest.raises(httpx.HTTPError):
            await http.get("/test")
        await http.close()

    @pytest.mark.asyncio
    async def test_error_log_does_not_contain_api_key(self):
        http = InternalHttpClient(
            internal_api_key="super-secret-key-12345",
            base_url="http://localhost:1",
            timeout_seconds=0.1,
        )
        with patch("inferia.common.http_client.logger") as mock_logger:
            with pytest.raises(httpx.HTTPError):
                await http.get("/test")
            # Check that the error log message doesn't contain the API key
            if mock_logger.error.called:
                log_msg = str(mock_logger.error.call_args)
                assert "super-secret-key-12345" not in log_msg
        await http.close()
