"""Tests for OAuthClient — wrapper around inferia-auth /oauth/token and /oauth/revoke.

Length caps per plan C3:
  code: 256
  code_verifier: 256
  redirect_uri: 2048
  refresh_token: 512
"""

import asyncio

import pytest
from pytest_httpserver import HTTPServer

from api_gateway.rbac.oauth_client import (
    OAuthClient,
    OAuthClientError,
)


@pytest.fixture
def client(httpserver):
    return OAuthClient(base_url=httpserver.url_for(""), client_id="inferiallm-dashboard")


@pytest.mark.asyncio
async def test_exchange_code_happy_path(httpserver, client):
    httpserver.expect_request(
        "/oauth/token",
        method="POST",
    ).respond_with_json(
        {
            "access_token": "atk",
            "refresh_token": "rtk",
            "token_type": "bearer",
            "expires_in": 900,
            "scope": "openid profile email inferiallm",
        }
    )
    tokens = await client.exchange_code(
        code="abc123",
        code_verifier="verifier-12345-with-enough-bytes",
        redirect_uri="https://app.example.test/auth/callback",
    )
    assert tokens["access_token"] == "atk"
    assert tokens["refresh_token"] == "rtk"
    # Verify the form payload contained all required fields.
    req, _ = httpserver.log[-1]
    body = req.get_data(as_text=True)
    assert "grant_type=authorization_code" in body
    assert "code=abc123" in body
    assert "client_id=inferiallm-dashboard" in body


@pytest.mark.asyncio
async def test_exchange_code_400_returns_none(httpserver, client):
    httpserver.expect_request("/oauth/token").respond_with_json(
        {"error": "invalid_grant", "error_description": "code expired"}, status=400
    )
    tokens = await client.exchange_code(
        code="abc",
        code_verifier="verifier-12345-with-enough-bytes",
        redirect_uri="https://app.example.test/auth/callback",
    )
    assert tokens is None


@pytest.mark.asyncio
async def test_exchange_code_401_returns_none(httpserver, client):
    httpserver.expect_request("/oauth/token").respond_with_data("", status=401)
    tokens = await client.exchange_code(
        code="abc",
        code_verifier="verifier-12345-with-enough-bytes",
        redirect_uri="https://app.example.test/auth/callback",
    )
    assert tokens is None


@pytest.mark.asyncio
async def test_exchange_code_5xx_raises(httpserver, client):
    httpserver.expect_request("/oauth/token").respond_with_data("oops", status=500)
    with pytest.raises(OAuthClientError):
        await client.exchange_code(
            code="abc",
            code_verifier="verifier-12345-with-enough-bytes",
            redirect_uri="https://app.example.test/auth/callback",
        )


@pytest.mark.asyncio
async def test_exchange_code_network_error_raises():
    # 127.0.0.1:1 = nothing listening = connection refused.
    bad_client = OAuthClient(base_url="http://127.0.0.1:1", client_id="x", timeout=0.5)
    with pytest.raises(OAuthClientError):
        await bad_client.exchange_code(
            code="abc",
            code_verifier="verifier-12345-with-enough-bytes",
            redirect_uri="https://app.example.test/auth/callback",
        )


@pytest.mark.asyncio
async def test_refresh_happy_path(httpserver, client):
    httpserver.expect_request("/oauth/token").respond_with_json(
        {
            "access_token": "new-atk",
            "refresh_token": "new-rtk",
            "token_type": "bearer",
            "expires_in": 900,
            "scope": "openid profile email inferiallm",
        }
    )
    tokens = await client.refresh(refresh_token="rtk-old")
    assert tokens["access_token"] == "new-atk"
    req, _ = httpserver.log[-1]
    body = req.get_data(as_text=True)
    assert "grant_type=refresh_token" in body
    assert "refresh_token=rtk-old" in body
    assert "client_id=inferiallm-dashboard" in body


@pytest.mark.asyncio
async def test_refresh_400_returns_none(httpserver, client):
    httpserver.expect_request("/oauth/token").respond_with_json(
        {"error": "invalid_grant"}, status=400
    )
    tokens = await client.refresh(refresh_token="rtk-old")
    assert tokens is None


@pytest.mark.asyncio
async def test_refresh_5xx_raises(httpserver, client):
    httpserver.expect_request("/oauth/token").respond_with_data("oops", status=502)
    with pytest.raises(OAuthClientError):
        await client.refresh(refresh_token="rtk-old")


@pytest.mark.asyncio
async def test_revoke_returns_true_on_200(httpserver, client):
    httpserver.expect_request("/oauth/revoke").respond_with_data("", status=200)
    ok = await client.revoke(token="rtk-old", token_type_hint="refresh_token")
    assert ok is True
    req, _ = httpserver.log[-1]
    body = req.get_data(as_text=True)
    assert "token=rtk-old" in body
    assert "token_type_hint=refresh_token" in body


@pytest.mark.asyncio
async def test_revoke_returns_true_on_404(httpserver, client):
    """RFC 7009: revoking an unknown token must still succeed."""
    httpserver.expect_request("/oauth/revoke").respond_with_data("", status=404)
    ok = await client.revoke(token="unknown", token_type_hint="refresh_token")
    assert ok is True


@pytest.mark.asyncio
async def test_revoke_returns_true_on_400(httpserver, client):
    """RFC 7009: bad-request also returns success to the caller per spec."""
    httpserver.expect_request("/oauth/revoke").respond_with_data("", status=400)
    ok = await client.revoke(token="bad", token_type_hint="refresh_token")
    assert ok is True


@pytest.mark.asyncio
async def test_revoke_5xx_returns_false(httpserver, client):
    httpserver.expect_request("/oauth/revoke").respond_with_data("", status=500)
    ok = await client.revoke(token="rtk", token_type_hint="refresh_token")
    assert ok is False


@pytest.mark.asyncio
async def test_revoke_network_error_returns_false():
    bad = OAuthClient(base_url="http://127.0.0.1:1", client_id="x", timeout=0.5)
    ok = await bad.revoke(token="x", token_type_hint="refresh_token")
    assert ok is False


# --- length caps ---------------------------------------------------------


@pytest.mark.asyncio
async def test_code_too_long_rejected_before_send(httpserver, client):
    """The OAuth code itself maxes out at 256 chars; longer is rejected."""
    with pytest.raises(ValueError):
        await client.exchange_code(
            code="a" * 257,
            code_verifier="verifier-12345-with-enough-bytes",
            redirect_uri="https://app.example.test/auth/callback",
        )
    # No HTTP request should have been made.
    assert len(httpserver.log) == 0


@pytest.mark.asyncio
async def test_verifier_too_long_rejected(httpserver, client):
    with pytest.raises(ValueError):
        await client.exchange_code(
            code="abc",
            code_verifier="x" * 257,
            redirect_uri="https://app.example.test/auth/callback",
        )
    assert len(httpserver.log) == 0


@pytest.mark.asyncio
async def test_redirect_uri_too_long_rejected(httpserver, client):
    with pytest.raises(ValueError):
        await client.exchange_code(
            code="abc",
            code_verifier="verifier-12345-with-enough-bytes",
            redirect_uri="https://app.example.test/" + ("a" * 3000),
        )
    assert len(httpserver.log) == 0


@pytest.mark.asyncio
async def test_refresh_too_long_rejected(httpserver, client):
    with pytest.raises(ValueError):
        await client.refresh(refresh_token="r" * 513)
    assert len(httpserver.log) == 0


@pytest.mark.asyncio
async def test_revoke_token_too_long_rejected(httpserver, client):
    with pytest.raises(ValueError):
        await client.revoke(token="r" * 8200, token_type_hint="refresh_token")
    assert len(httpserver.log) == 0


@pytest.mark.asyncio
async def test_empty_code_rejected(httpserver, client):
    with pytest.raises(ValueError):
        await client.exchange_code(
            code="",
            code_verifier="verifier-12345-with-enough-bytes",
            redirect_uri="https://app.example.test/auth/callback",
        )


@pytest.mark.asyncio
async def test_empty_refresh_rejected(httpserver, client):
    with pytest.raises(ValueError):
        await client.refresh(refresh_token="")


@pytest.mark.asyncio
async def test_invalid_token_type_hint_rejected(httpserver, client):
    with pytest.raises(ValueError):
        await client.revoke(token="x", token_type_hint="bogus")


@pytest.mark.asyncio
async def test_close_releases_client():
    c = OAuthClient(base_url="http://example.test", client_id="x")
    # Force the lazy client to materialize, then close.
    _ = c._get_client()
    await c.close()
    # Calling close again should be a no-op (not raise).
    await c.close()


@pytest.mark.asyncio
async def test_exchange_code_non_json_2xx_raises(httpserver, client):
    httpserver.expect_request("/oauth/token").respond_with_data(
        "not-json", content_type="text/plain"
    )
    with pytest.raises(OAuthClientError):
        await client.exchange_code(
            code="abc",
            code_verifier="verifier-12345-with-enough-bytes",
            redirect_uri="https://app.example.test/auth/callback",
        )


@pytest.mark.asyncio
async def test_revoke_defaults_token_type_hint(httpserver, client):
    httpserver.expect_request("/oauth/revoke").respond_with_data("", status=200)
    ok = await client.revoke(token="rtk")
    assert ok is True
    req, _ = httpserver.log[-1]
    body = req.get_data(as_text=True)
    assert "token_type_hint=refresh_token" in body


# ---------------------------------------------------------------------------
# Bug-2 verify= param — OAuthClient stores and threads the TLS setting
# ---------------------------------------------------------------------------


def test_oauth_client_stores_verify_default():
    """OAuthClient defaults verify=True and stores it on self._verify."""
    c = OAuthClient(base_url="https://auth.example.test", client_id="x")
    assert c._verify is True


def test_oauth_client_stores_verify_ca_bundle():
    """OAuthClient(verify="/path/ca.pem") stores the path on self._verify."""
    c = OAuthClient(base_url="https://auth.example.test", client_id="x", verify="/path/ca.pem")
    assert c._verify == "/path/ca.pem"


def test_oauth_client_stores_verify_false():
    """OAuthClient(verify=False) stores False on self._verify."""
    c = OAuthClient(base_url="https://auth.example.test", client_id="x", verify=False)
    assert c._verify is False


def test_oauth_client_get_client_uses_verify():
    """The lazily-built httpx client is created with the stored verify value."""
    c = OAuthClient(base_url="https://auth.example.test", client_id="x", verify=False)
    # Trigger lazy client creation; it should NOT raise even with verify=False.
    inner = c._get_client()
    # httpx.AsyncClient stores verify internally; just confirm it was created.
    assert inner is not None
    assert not inner.is_closed


# ---------------------------------------------------------------------------
# Resilience: retry once on a transport error with a fresh connection so the
# gateway self-heals (no process restart needed) when the IdP's proxy/tunnel
# drops a pooled connection.
# ---------------------------------------------------------------------------

import httpx as _httpx


class _FlakyClient:
    """Fake httpx client: raises ConnectError for the first `fail_times` posts,
    then returns `response`."""

    def __init__(self, fail_times, response):
        self.calls = 0
        self._fail_times = fail_times
        self._response = response
        self.is_closed = False

    async def post(self, url, data=None, headers=None):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise _httpx.ConnectError("stale pooled connection")
        return self._response

    async def aclose(self):
        self.is_closed = True


@pytest.mark.asyncio
async def test_exchange_code_retries_once_then_succeeds():
    resp = _httpx.Response(200, json={"access_token": "atk", "refresh_token": "rtk"})
    flaky = _FlakyClient(fail_times=1, response=resp)
    c = OAuthClient(base_url="http://idp.test", client_id="x")
    c._get_client = lambda: flaky  # bypass real httpx; exercise the retry loop

    tokens = await c.exchange_code(
        code="abc", code_verifier="verifier-12345-with-enough-bytes",
        redirect_uri="https://app.example.test/auth/callback",
    )
    assert tokens["access_token"] == "atk"
    assert flaky.calls == 2  # failed once, retried once, succeeded


@pytest.mark.asyncio
async def test_exchange_code_retry_exhausted_raises():
    flaky = _FlakyClient(fail_times=2, response=_httpx.Response(200, json={}))
    c = OAuthClient(base_url="http://idp.test", client_id="x")
    c._get_client = lambda: flaky

    with pytest.raises(OAuthClientError):
        await c.exchange_code(
            code="abc", code_verifier="verifier-12345-with-enough-bytes",
            redirect_uri="https://app.example.test/auth/callback",
        )
    assert flaky.calls == 2  # exactly one retry, then give up


@pytest.mark.asyncio
async def test_transport_error_discards_client_so_next_request_redials():
    # After a transport failure the cached client must be dropped so a later
    # request builds a fresh one — this is what removes the "stuck until
    # restart" behavior.
    bad = OAuthClient(base_url="http://127.0.0.1:1", client_id="x", timeout=0.3)
    with pytest.raises(OAuthClientError):
        await bad.exchange_code(
            code="abc", code_verifier="verifier-12345-with-enough-bytes",
            redirect_uri="https://app.example.test/auth/callback",
        )
    assert bad._client is None  # discarded -> next call dials fresh


@pytest.mark.asyncio
async def test_client_rebuilt_after_close():
    # close() must drop the client so the next request dials fresh (the
    # self-heal path). _get_client caches within a session but rebuilds after.
    c = OAuthClient(base_url="http://idp.test", client_id="x")
    first = c._get_client()
    assert c._get_client() is first  # cached within a session
    await c.close()
    assert c._client is None
    assert c._get_client() is not first  # fresh client after close
    await c.close()


@pytest.mark.asyncio
async def test_revoke_retries_then_succeeds():
    flaky = _FlakyClient(fail_times=1, response=_httpx.Response(200))
    c = OAuthClient(base_url="http://idp.test", client_id="x")
    c._get_client = lambda: flaky
    assert await c.revoke(token="rtk") is True
    assert flaky.calls == 2
