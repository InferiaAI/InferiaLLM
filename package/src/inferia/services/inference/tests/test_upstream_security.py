"""Tests for upstream proxy security validators and integration."""

import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager

from fastapi import HTTPException

from inferia.services.inference.core.validators import (
    validate_upstream_url,
    sanitize_headers,
    strip_hop_by_hop_headers,
    check_response_size,
)


# ---------------------------------------------------------------------------
# URL Validation
# ---------------------------------------------------------------------------


class TestValidateUpstreamUrl:
    """Verify upstream URL validation catches injection and SSRF."""

    def test_valid_http_url_passes(self):
        url = "http://provider.example.com:8080/v1/chat/completions"
        assert validate_upstream_url(url, []) == url

    def test_valid_https_url_passes(self):
        url = "https://api.openai.com/v1/chat/completions"
        assert validate_upstream_url(url, []) == url

    def test_non_http_scheme_rejected(self):
        for scheme in ("ftp", "file", "gopher", "data"):
            with pytest.raises(ValueError, match="scheme"):
                validate_upstream_url(f"{scheme}://evil.com/path", [])

    def test_crlf_in_url_rejected(self):
        with pytest.raises(ValueError, match="illegal characters"):
            validate_upstream_url("http://legit.com\r\nHost: evil.com/path", [])

        with pytest.raises(ValueError, match="illegal characters"):
            validate_upstream_url("http://legit.com\nX-Injected: true", [])

    def test_embedded_credentials_rejected(self):
        with pytest.raises(ValueError, match="credentials"):
            validate_upstream_url("http://user:pass@provider.com/v1", [])

    def test_private_ipv4_rejected(self):
        private_ips = [
            "http://127.0.0.1:8080/v1",
            "http://10.0.0.5:8080/v1",
            "http://172.16.0.1:8080/v1",
            "http://192.168.1.1:8080/v1",
            "http://169.254.169.254/latest/meta-data",
        ]
        for url in private_ips:
            with pytest.raises(ValueError, match="not allowed"):
                validate_upstream_url(url, [])

    def test_private_ipv6_rejected(self):
        with pytest.raises(ValueError, match="not allowed"):
            validate_upstream_url("http://[::1]:8080/v1", [])

    def test_allowlisted_private_ip_passes(self):
        url = "http://10.0.1.50:8080/v1/chat/completions"
        result = validate_upstream_url(url, ["10.0.1.50"])
        assert result == url

    def test_non_allowlisted_private_ip_still_blocked(self):
        """Allowlist for one host doesn't open all private IPs."""
        with pytest.raises(ValueError, match="not allowed"):
            validate_upstream_url("http://10.0.0.99:8080/v1", ["10.0.1.50"])


# ---------------------------------------------------------------------------
# Header Sanitization
# ---------------------------------------------------------------------------


class TestSanitizeHeaders:
    """Verify CRLF injection in headers is blocked."""

    def test_clean_headers_unchanged(self):
        headers = {"Content-Type": "application/json", "Authorization": "Bearer xyz"}
        assert sanitize_headers(headers) == headers

    def test_crlf_in_value_dropped(self):
        headers = {
            "Content-Type": "application/json",
            "X-Evil": "value\r\nInjected: true",
        }
        result = sanitize_headers(headers)
        assert "X-Evil" not in result
        assert result["Content-Type"] == "application/json"

    def test_crlf_in_key_dropped(self):
        headers = {"Good-Header": "ok", "Bad\r\nHeader": "value"}
        result = sanitize_headers(headers)
        assert "Bad\r\nHeader" not in result
        assert result["Good-Header"] == "ok"


# ---------------------------------------------------------------------------
# Hop-by-Hop Header Stripping
# ---------------------------------------------------------------------------


class TestStripHopByHopHeaders:
    """Verify hop-by-hop headers are removed."""

    def test_hop_by_hop_removed_others_preserved(self):
        headers = {
            "Content-Type": "application/json",
            "Connection": "keep-alive",
            "Keep-Alive": "timeout=5",
            "Transfer-Encoding": "chunked",
            "X-Request-Id": "abc123",
        }
        result = strip_hop_by_hop_headers(headers)
        assert "Connection" not in result
        assert "Keep-Alive" not in result
        assert "Transfer-Encoding" not in result
        assert result["Content-Type"] == "application/json"
        assert result["X-Request-Id"] == "abc123"


# ---------------------------------------------------------------------------
# Response Size Check
# ---------------------------------------------------------------------------


class TestCheckResponseSize:
    """Verify response size limit enforcement."""

    def test_within_limit_passes(self):
        check_response_size(1000, 50_000_000)  # No exception

    def test_none_content_length_passes(self):
        check_response_size(None, 50_000_000)  # No exception

    def test_exceeding_limit_raises(self):
        with pytest.raises(ValueError, match="exceeds limit"):
            check_response_size(100_000_000, 50_000_000)


# ---------------------------------------------------------------------------
# TLS Warning
# ---------------------------------------------------------------------------


class TestTLSWarning:
    """Verify TLS disable warning is logged."""

    def test_tls_disabled_logs_warning(self):
        from inferia.services.inference.core.http_client import HttpClientManager

        with patch(
            "inferia.services.inference.core.http_client.settings"
        ) as mock_settings, patch(
            "inferia.services.inference.core.http_client.logger"
        ) as mock_logger:
            mock_settings.verify_ssl = False
            mock_settings.upstream_http_timeout_seconds = 60.0
            mock_settings.upstream_http_connect_timeout_seconds = 10.0
            mock_settings.upstream_http_max_connections = 500
            mock_settings.upstream_http_max_keepalive_connections = 100

            # Force new client creation
            HttpClientManager._client = None
            HttpClientManager.get_client()
            HttpClientManager._client = None  # Clean up

            mock_logger.warning.assert_called_once()
            assert "TLS verification disabled" in mock_logger.warning.call_args[0][0]


# ---------------------------------------------------------------------------
# Integration: call_upstream with SSRF
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _noop_limit(key):
    yield


class TestCallUpstreamSecurity:
    """Verify security checks integrate into call_upstream."""

    @pytest.mark.asyncio
    async def test_call_upstream_private_ip_raises_400(self):
        from inferia.services.inference.core.service import GatewayService

        with patch(
            "inferia.services.inference.core.service.settings"
        ) as mock_settings, patch(
            "inferia.services.inference.core.service.upstream_concurrency_limiter"
        ) as mock_limiter:
            mock_settings.upstream_allowed_internal_hosts = ""
            mock_settings.upstream_max_response_bytes = 52_428_800
            mock_limiter.limit = _noop_limit

            with pytest.raises(HTTPException) as exc:
                await GatewayService.call_upstream(
                    endpoint_url="http://127.0.0.1:8080",
                    payload={"messages": []},
                    headers={"Content-Type": "application/json"},
                )
            assert exc.value.status_code == 400
            assert "not allowed" in exc.value.detail

    @pytest.mark.asyncio
    async def test_call_upstream_with_path_param_also_validates(self):
        """The path= parameter branch also gets URL validation."""
        from inferia.services.inference.core.service import GatewayService

        with patch(
            "inferia.services.inference.core.service.settings"
        ) as mock_settings, patch(
            "inferia.services.inference.core.service.upstream_concurrency_limiter"
        ) as mock_limiter:
            mock_settings.upstream_allowed_internal_hosts = ""
            mock_settings.upstream_max_response_bytes = 52_428_800
            mock_limiter.limit = _noop_limit

            with pytest.raises(HTTPException) as exc:
                await GatewayService.call_upstream(
                    endpoint_url="http://10.0.0.1:8080",
                    payload={"messages": []},
                    headers={"Content-Type": "application/json"},
                    path="/v1/embeddings",
                )
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_stream_upstream_private_ip_yields_error(self):
        """stream_upstream yields SSE error for blocked hosts."""
        from inferia.services.inference.core.service import GatewayService

        with patch(
            "inferia.services.inference.core.service.settings"
        ) as mock_settings:
            mock_settings.upstream_allowed_internal_hosts = ""
            mock_settings.upstream_max_response_bytes = 52_428_800

            chunks = []
            async for chunk in GatewayService.stream_upstream(
                endpoint_url="http://192.168.1.1:8080",
                payload={"messages": []},
                headers={"Content-Type": "application/json"},
            ):
                chunks.append(chunk)

            assert len(chunks) == 1
            assert b"Invalid upstream configuration" in chunks[0]

    @pytest.mark.asyncio
    async def test_stream_upstream_oversized_response_aborted(self):
        """Streaming aborts when byte counter exceeds limit."""
        from inferia.services.inference.core.service import GatewayService

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        # Simulate chunks that exceed a small limit
        mock_response.aiter_raw = MagicMock(
            return_value=self._async_iter([b"x" * 500, b"x" * 500, b"x" * 500])
        )

        @asynccontextmanager
        async def mock_stream(*args, **kwargs):
            yield mock_response

        mock_client = MagicMock()
        mock_client.stream = mock_stream

        with patch(
            "inferia.services.inference.core.service.settings"
        ) as mock_settings, patch(
            "inferia.services.inference.core.service.http_client"
        ) as mock_hc, patch(
            "inferia.services.inference.core.service.upstream_concurrency_limiter"
        ) as mock_limiter:
            mock_settings.upstream_allowed_internal_hosts = ""
            mock_settings.upstream_max_response_bytes = 1000  # Small limit
            mock_hc.get_client.return_value = mock_client
            mock_limiter.limit = _noop_limit

            chunks = []
            async for chunk in GatewayService.stream_upstream(
                endpoint_url="https://api.example.com",
                payload={"messages": []},
                headers={"Content-Type": "application/json"},
            ):
                chunks.append(chunk)

            # Should have first two data chunks (1000 bytes) then error
            assert any(b"exceeded size limit" in c for c in chunks)

    @staticmethod
    async def _async_iter(items):
        for item in items:
            yield item
