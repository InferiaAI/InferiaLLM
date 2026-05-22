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

from inferia.services.api_gateway.rbac.oauth_client import (
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
