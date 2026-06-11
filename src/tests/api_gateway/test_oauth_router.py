"""Tests for /auth/start and /auth/callback (PKCE OAuth2 entry/exit).

The router is wired into a fresh FastAPI app per test to keep state
isolated and avoid colliding with the main gateway app's auth middleware.
"""

import hashlib
import base64
from typing import AsyncIterator
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _build_app(monkeypatch, *, auth_provider="external", base="https://auth.example.test",
               redirect_uri="https://app.example.test/auth/callback",
               client_id="inferiallm-dashboard") -> FastAPI:
    """Wire the oauth_router into a clean FastAPI app with patched settings."""
    from services.api_gateway.config import settings

    monkeypatch.setattr(settings, "auth_provider", auth_provider, raising=False)
    monkeypatch.setattr(settings, "external_auth_url", base, raising=False)
    monkeypatch.setattr(settings, "oauth_client_id", client_id, raising=False)
    monkeypatch.setattr(settings, "oauth_redirect_uri", redirect_uri, raising=False)
    monkeypatch.setattr(settings, "app_namespace", "inferiallm", raising=False)

    # Reset the module-level oauth_client singleton so a freshly imported
    # settings is picked up.
    from services.api_gateway.rbac import oauth_router as r
    r._oauth_client = None

    app = FastAPI()
    app.include_router(r.router)
    return app


@pytest_asyncio.fixture
async def client(monkeypatch) -> AsyncIterator[AsyncClient]:
    app = _build_app(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        yield c


@pytest.mark.asyncio
async def test_start_happy_path(client):
    r = await client.get("/auth/start", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    parsed = urlparse(loc)
    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.example.test"
    assert parsed.path == "/oauth/authorize"
    qs = parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["inferiallm-dashboard"]
    assert qs["redirect_uri"] == ["https://app.example.test/auth/callback"]
    assert qs["code_challenge_method"] == ["S256"]
    assert "code_challenge" in qs
    assert "state" in qs
    assert qs["scope"] == ["openid profile email inferiallm"]
    # State cookie is set.
    cookies = r.cookies
    assert "oauth_state" in cookies
    assert "oauth_verifier" in cookies
    # State value matches query param.
    assert cookies["oauth_state"] == qs["state"][0]


@pytest.mark.asyncio
async def test_start_state_is_random_each_time(client):
    r1 = await client.get("/auth/start", follow_redirects=False)
    # Different cookie jar per call → use a fresh client to avoid Set-Cookie reuse.
    r2 = await client.get("/auth/start", follow_redirects=False)
    state1 = r1.cookies["oauth_state"]
    state2 = r2.cookies["oauth_state"]
    assert state1 != state2
    assert len(state1) >= 32
    assert len(state2) >= 32


@pytest.mark.asyncio
async def test_start_503_when_not_external_mode(monkeypatch):
    app = _build_app(monkeypatch, auth_provider="local")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        r = await c.get("/auth/start", follow_redirects=False)
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_start_pkce_challenge_matches_verifier(client):
    r = await client.get("/auth/start", follow_redirects=False)
    parsed = urlparse(r.headers["location"])
    qs = parse_qs(parsed.query)
    verifier = r.cookies["oauth_verifier"]
    challenge = qs["code_challenge"][0]
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    assert challenge == expected


# --- callback ----------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_happy_path(monkeypatch):
    app = _build_app(monkeypatch)
    from services.api_gateway.rbac import oauth_router as r
    fake_client = AsyncMock()
    fake_client.exchange_code = AsyncMock(
        return_value={"access_token": "atk", "refresh_token": "rtk",
                       "expires_in": 900, "token_type": "bearer",
                       "scope": "openid profile email inferiallm"}
    )
    monkeypatch.setattr(r, "_get_oauth_client", lambda: fake_client)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        c.cookies.set("oauth_state", "state-x")
        c.cookies.set("oauth_verifier", "verifier-12345-with-enough-bytes")
        resp = await c.get(
            "/auth/callback",
            params={"code": "abc123", "state": "state-x"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/#access_token=atk"
    # refresh cookie set
    assert resp.cookies.get("refresh_token") == "rtk"
    # state/verifier cookies cleared (max-age=0 → deleted)
    set_cookies = resp.headers.get_list("set-cookie")
    has_state_clear = any("oauth_state=" in c and ("max-age=0" in c.lower()) for c in set_cookies)
    has_verifier_clear = any("oauth_verifier=" in c and ("max-age=0" in c.lower()) for c in set_cookies)
    assert has_state_clear
    assert has_verifier_clear


@pytest.mark.asyncio
async def test_callback_missing_state_cookie(monkeypatch):
    app = _build_app(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        c.cookies.set("oauth_verifier", "verifier-12345-with-enough-bytes")
        resp = await c.get(
            "/auth/callback",
            params={"code": "abc", "state": "state-x"},
            follow_redirects=False,
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_callback_mismatched_state(monkeypatch):
    app = _build_app(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        c.cookies.set("oauth_state", "state-cookie")
        c.cookies.set("oauth_verifier", "verifier-12345-with-enough-bytes")
        resp = await c.get(
            "/auth/callback",
            params={"code": "abc", "state": "state-query"},
            follow_redirects=False,
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_callback_missing_verifier_cookie(monkeypatch):
    app = _build_app(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        c.cookies.set("oauth_state", "state-x")
        resp = await c.get(
            "/auth/callback",
            params={"code": "abc", "state": "state-x"},
            follow_redirects=False,
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_callback_exchange_returns_none_502(monkeypatch):
    app = _build_app(monkeypatch)
    from services.api_gateway.rbac import oauth_router as r
    fake_client = AsyncMock()
    fake_client.exchange_code = AsyncMock(return_value=None)
    monkeypatch.setattr(r, "_get_oauth_client", lambda: fake_client)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        c.cookies.set("oauth_state", "state-x")
        c.cookies.set("oauth_verifier", "verifier-12345-with-enough-bytes")
        resp = await c.get(
            "/auth/callback",
            params={"code": "abc", "state": "state-x"},
            follow_redirects=False,
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_callback_exchange_raises_502(monkeypatch):
    app = _build_app(monkeypatch)
    from services.api_gateway.rbac import oauth_router as r
    from services.api_gateway.rbac.oauth_client import OAuthClientError
    fake_client = AsyncMock()
    fake_client.exchange_code = AsyncMock(side_effect=OAuthClientError("boom"))
    monkeypatch.setattr(r, "_get_oauth_client", lambda: fake_client)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        c.cookies.set("oauth_state", "state-x")
        c.cookies.set("oauth_verifier", "verifier-12345-with-enough-bytes")
        resp = await c.get(
            "/auth/callback",
            params={"code": "abc", "state": "state-x"},
            follow_redirects=False,
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_callback_code_too_long_rejected_before_network(monkeypatch):
    """A 'code' query param longer than 256 chars must be rejected with 400
    before any HTTP call is attempted."""
    app = _build_app(monkeypatch)
    from services.api_gateway.rbac import oauth_router as r
    fake_client = AsyncMock()
    fake_client.exchange_code = AsyncMock()
    monkeypatch.setattr(r, "_get_oauth_client", lambda: fake_client)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        c.cookies.set("oauth_state", "state-x")
        c.cookies.set("oauth_verifier", "verifier-12345-with-enough-bytes")
        resp = await c.get(
            "/auth/callback",
            params={"code": "a" * 257, "state": "state-x"},
            follow_redirects=False,
        )
    assert resp.status_code == 400
    fake_client.exchange_code.assert_not_called()


@pytest.mark.asyncio
async def test_callback_state_query_too_long_rejected(monkeypatch):
    """State param in query must also be length-capped."""
    app = _build_app(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        c.cookies.set("oauth_state", "state-x")
        c.cookies.set("oauth_verifier", "verifier-12345-with-enough-bytes")
        resp = await c.get(
            "/auth/callback",
            params={"code": "abc", "state": "s" * 1025},
            follow_redirects=False,
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_callback_503_when_not_external_mode(monkeypatch):
    app = _build_app(monkeypatch, auth_provider="local")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        c.cookies.set("oauth_state", "state-x")
        c.cookies.set("oauth_verifier", "verifier-12345-with-enough-bytes")
        resp = await c.get(
            "/auth/callback",
            params={"code": "abc", "state": "state-x"},
            follow_redirects=False,
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_callback_missing_code_param(monkeypatch):
    """FastAPI should 422 a missing required query parameter."""
    app = _build_app(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        c.cookies.set("oauth_state", "state-x")
        c.cookies.set("oauth_verifier", "verifier-12345-with-enough-bytes")
        resp = await c.get(
            "/auth/callback",
            params={"state": "state-x"},
            follow_redirects=False,
        )
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_callback_missing_state_param(monkeypatch):
    app = _build_app(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        c.cookies.set("oauth_state", "state-x")
        c.cookies.set("oauth_verifier", "verifier-12345-with-enough-bytes")
        resp = await c.get(
            "/auth/callback",
            params={"code": "abc"},
            follow_redirects=False,
        )
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_start_redirects_to_configured_authorize_url_with_port(monkeypatch):
    app = _build_app(monkeypatch, base="https://auth.example.test:8443")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        r = await c.get("/auth/start", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://auth.example.test:8443/oauth/authorize?")


@pytest.mark.asyncio
async def test_get_oauth_client_singleton_returns_same_instance(monkeypatch):
    """Lazily-built singleton must reuse the same client across calls."""
    monkeypatch.setattr("services.api_gateway.config.settings.external_auth_url",
                        "https://auth.example.test", raising=False)
    monkeypatch.setattr("services.api_gateway.config.settings.oauth_client_id",
                        "inferiallm-dashboard", raising=False)
    from services.api_gateway.rbac import oauth_router as r
    r._oauth_client = None
    a = r._get_oauth_client()
    b = r._get_oauth_client()
    assert a is b
