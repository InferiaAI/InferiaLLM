"""Tests for upstream proxy security validators and integration."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager

from fastapi import HTTPException

from inference.core.validators import (
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
        from inference.core.http_client import HttpClientManager

        with patch(
            "inference.core.http_client.settings"
        ) as mock_settings, patch(
            "inference.core.http_client.logger"
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
        from inference.core.service import GatewayService

        with patch(
            "inference.core.service.settings"
        ) as mock_settings, patch(
            "inference.core.service.upstream_concurrency_limiter"
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
        from inference.core.service import GatewayService

        with patch(
            "inference.core.service.settings"
        ) as mock_settings, patch(
            "inference.core.service.upstream_concurrency_limiter"
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
        from inference.core.service import GatewayService

        with patch(
            "inference.core.service.settings"
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
        from inference.core.service import GatewayService

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        # Simulate chunks that exceed a small limit
        mock_response.aiter_raw = MagicMock(
            return_value=self._async_iter([b"x" * 500, b"x" * 500, b"x" * 500])
        )
        mock_response.aclose = AsyncMock()

        mock_client = MagicMock()
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.send = AsyncMock(return_value=mock_response)

        with patch(
            "inference.core.service.settings"
        ) as mock_settings, patch(
            "inference.core.service.http_client"
        ) as mock_hc, patch(
            "inference.core.service.upstream_concurrency_limiter"
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


class TestAddressFormsThatReachInternalHosts:
    """Spellings the resolver accepts for an internal address, and the names
    that mean this machine or the cloud metadata service."""

    @pytest.mark.parametrize("host", [
        "2852039166",            # 169.254.169.254 as one number
        "0xa9fea9fe",            # ... in hex
        "0251.0376.0251.0376",   # ... in octal
        "127.1",
        "0.0.0.0",
        "[::ffff:169.254.169.254]",
        "[::]",
        "[fe80::1]",
        "100.64.0.1",            # carrier-grade NAT
        "localhost",
        "api.localhost",
        "metadata.google.internal",
    ])
    def test_rejected(self, host):
        with pytest.raises(ValueError, match="not allowed"):
            validate_upstream_url(f"http://{host}/latest/meta-data/", [])

    def test_an_allowlisted_name_still_passes(self):
        assert validate_upstream_url("http://localhost:11434/v1", ["localhost"])


@pytest.mark.asyncio
class TestNamesAreCheckedByWhatTheyResolveTo:

    async def _check(self, url, allowed, addresses):
        from inference.core import validators

        async def _resolve(hostname):
            return addresses

        with patch.object(validators, "_resolve", _resolve):
            return await validators.check_resolved_host(url, allowed)

    @pytest.mark.parametrize("address", ["169.254.169.254", "10.0.0.5", "127.0.0.1", "fd00::1"])
    async def test_a_name_resolving_inside_is_rejected(self, address):
        with pytest.raises(ValueError, match="not allowed"):
            await self._check("http://rebind.example.com/v1", [], [address])

    async def test_one_internal_answer_is_enough(self):
        with pytest.raises(ValueError, match="not allowed"):
            await self._check("http://x.example.com", [], ["93.184.215.14", "10.0.0.5"])

    async def test_a_public_name_passes_with_the_address_to_use(self):
        url = "https://api.example.com/v1"
        assert await self._check(url, [], ["93.184.215.14"]) == "93.184.215.14"

    async def test_allowed_by_name_is_not_pinned(self):
        url = "http://ollama.lan:11434/v1"
        assert await self._check(url, ["ollama.lan"], ["192.168.1.20"]) is None

    async def test_allowed_by_address(self):
        """setup.sh lists kind node addresses, which names may resolve to."""
        url = "http://node.kind:30080/v1"
        assert await self._check(url, ["172.18.0.3"], ["172.18.0.3"]) == "172.18.0.3"

    async def test_a_name_that_does_not_resolve_is_not_pinned(self):
        assert await self._check("http://nowhere.example.com", [], []) is None


@pytest.mark.asyncio
class TestPinnedRequest:
    """A checked name is requested at the checked address (DNS rebinding)."""

    def test_keeps_path_port_and_host(self):
        from inference.core.validators import pinned_request

        url, pin = pinned_request("https://api.example.com:8443/v1/chat?x=1", "93.184.215.14")
        assert url == "https://93.184.215.14:8443/v1/chat?x=1"
        assert pin == {"host": "api.example.com:8443",
                       "extensions": {"sni_hostname": "api.example.com"}}

    def test_brackets_ipv6(self):
        from inference.core.validators import pinned_request

        url, pin = pinned_request("http://api.example.com/v1", "2606:2800::1")
        assert url == "http://[2606:2800::1]/v1"
        assert pin["host"] == "api.example.com"

    async def test_the_request_goes_to_the_checked_address(self):
        """A second lookup can not move it: the client never resolves the name."""
        import httpx
        from inference.core import validators
        from inference.core.service import GatewayService

        answers = iter([["93.184.215.14"], ["169.254.169.254"]])

        async def _resolve(hostname):
            return next(answers)

        seen = {}

        def _handler(request):
            seen.update(host=request.url.host, header=request.headers["host"],
                        sni=request.extensions.get("sni_hostname"))
            return httpx.Response(200, json={})

        with patch.object(validators, "_resolve", _resolve),                 patch.object(GatewayService, "_get_allowed_hosts", return_value=[]):
            url, pin = await GatewayService._checked_url("https://rebind.example.com/v1", True)
            headers, kwargs = GatewayService._apply_pin({"authorization": "Bearer k"}, pin)
            async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as c:
                await c.post(url, json={}, headers=headers, **kwargs)

        assert seen == {"host": "93.184.215.14", "header": "rebind.example.com",
                        "sni": "rebind.example.com"}

    async def test_without_resolving_nothing_is_pinned(self):
        from inference.core.service import GatewayService

        with patch.object(GatewayService, "_get_allowed_hosts", return_value=[]):
            assert await GatewayService._checked_url("http://api.example.com/v1", False) == (
                "http://api.example.com/v1", {})


def test_the_operators_proxies_are_always_allowed():
    """Worker and external traffic go through these internal proxies when set."""
    from inference.core.service import GatewayService

    with patch("inference.core.service.settings") as s:
        s.upstream_allowed_internal_hosts = "10.0.1.50"
        s.envoy_url = "http://envoy:10000"
        s.external_proxy_url = "http://inferiagate:4000"
        assert GatewayService._get_allowed_hosts() == ["10.0.1.50", "envoy", "inferiagate"]


@pytest.mark.asyncio
async def test_video_status_checks_its_upstream():
    """The status poll is a GET whose JSON goes back to the caller."""
    from inference.core.handlers.video import VideoHandler

    context = {"valid": True, "deployment": {
        "engine": "inferia-diffusion", "endpoint": "http://2852039166/latest?",
    }}
    with (
        patch("inference.core.handlers.video.GatewayService.resolve_context",
              AsyncMock(return_value=context)),
        patch("inference.core.handlers.video.settings") as s,
        patch("inference.core.handlers.video.http_client") as hc,
    ):
        s.envoy_url = None
        with pytest.raises(HTTPException) as exc:
            await VideoHandler.handle_status("sk-x", "vid", "m")

    assert exc.value.status_code == 502
    hc.get_client.return_value.get.assert_not_called()


@pytest.mark.asyncio
class TestOnlyUserEnteredEndpointsAreResolved:
    """External deployments carry a URL a user typed; worker and k8s ones
    carry a URL the platform set, which often names a private host."""

    async def _checked(self, url, deployment):
        from inference.core import validators
        from inference.core.service import GatewayService

        async def _private(hostname):
            return ["172.18.0.7"]

        with (
            patch.object(validators, "_resolve", _private),
            patch("inference.core.service.settings") as s,
        ):
            s.upstream_allowed_internal_hosts = ""
            s.envoy_url = None
            s.external_proxy_url = None
            return await GatewayService._checked_url(
                url, GatewayService.resolves_names_for(deployment)
            )

    async def test_a_worker_hostname_still_works(self):
        url = "http://inferia-worker:8080/v1/chat/completions"
        assert await self._checked(url, {"pool_id": "pool-1"}) == (url, {})

    async def test_the_same_name_on_an_external_deployment_is_refused(self):
        with pytest.raises(ValueError, match="not allowed"):
            await self._checked("http://inferia-worker:8080/v1", {"pool_id": None})

    async def test_a_worker_still_gets_the_literal_checks(self):
        with pytest.raises(ValueError, match="not allowed"):
            await self._checked("http://2852039166/latest", {"pool_id": "pool-1"})


@pytest.mark.parametrize("host", [
    "metadata.google.internal.", "localhost.", "instance-data.ec2.internal",
    "metadata.goog", "169.254.169.254.",
])
def test_trailing_dots_and_more_metadata_names_are_rejected(host):
    """A trailing dot names the same host, so it must not slip past the name list."""
    with pytest.raises(ValueError, match="not allowed"):
        validate_upstream_url(f"http://{host}/", [])


def test_an_allowlisted_name_matches_with_a_trailing_dot():
    assert validate_upstream_url("http://ollama.lan./v1", ["ollama.lan"])
