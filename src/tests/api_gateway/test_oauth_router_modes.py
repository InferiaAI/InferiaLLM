"""Tests for /auth/start availability across the 3 auth modes.

Verifies that the redirect-SSO flow is available in BOTH external modes
(oidc, inferiaauth) and disabled (503) in local mode.

Run with --noconftest to avoid the shared-conftest jwt fixture conflict:
  PYTHONPATH=/home/celestix/.pyenv/versions/3.12.9/lib/python3.12/site-packages:src \\
    pytest src/tests/api_gateway/test_oauth_router_modes.py --noconftest
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api_gateway.rbac.oauth_router as orouter


def _client(monkeypatch, mode: str) -> TestClient:
    """Build a TestClient that mounts ONLY the oauth_router with mocked settings."""
    monkeypatch.setattr(orouter.settings, "auth_provider", mode, raising=False)
    monkeypatch.setattr(orouter.settings, "oauth_client_id", "inferiallm-dashboard", raising=False)
    monkeypatch.setattr(orouter.settings, "oauth_redirect_uri", "https://app/auth/callback", raising=False)
    monkeypatch.setattr(orouter.settings, "external_auth_url", "https://auth.example.com", raising=False)
    monkeypatch.setattr(orouter.settings, "app_namespace", "inferiallm", raising=False)

    # is_external_mode is a @property that derives from auth_provider — monkeypatching
    # auth_provider above is sufficient for it to return the correct value automatically.

    # Reset singleton oauth_client so any freshly patched settings are picked up.
    orouter._oauth_client = None

    app = FastAPI()
    app.include_router(orouter.router)
    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------------
# /auth/start — mode gates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["oidc", "inferiaauth"])
def test_auth_start_redirects_in_external_modes(monkeypatch, mode):
    """/auth/start must return 302 for oidc and inferiaauth modes."""
    r = _client(monkeypatch, mode).get("/auth/start")
    assert r.status_code == 302
    assert "/oauth/authorize" in r.headers["location"]


def test_auth_start_503_in_local_mode(monkeypatch):
    """/auth/start must return 503 when auth_provider is local."""
    r = _client(monkeypatch, "local").get("/auth/start")
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# /auth/callback — mode gates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["oidc", "inferiaauth"])
def test_auth_callback_reaches_logic_in_external_modes(monkeypatch, mode):
    """/auth/callback must NOT return 503 due to mode gate for oidc/inferiaauth.

    Without valid PKCE cookies it returns 400 (missing state) — that is past
    the mode guard, which is all we need to verify here.
    """
    r = _client(monkeypatch, mode).get("/auth/callback?code=abc&state=xyz")
    assert r.status_code != 503


def test_auth_callback_503_in_local_mode(monkeypatch):
    """/auth/callback must return 503 when auth_provider is local."""
    r = _client(monkeypatch, "local").get("/auth/callback?code=abc&state=xyz")
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# Redirect target sanity (oidc + inferiaauth share identical PKCE logic)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["oidc", "inferiaauth"])
def test_auth_start_location_points_to_authorize_endpoint(monkeypatch, mode):
    """The redirect URL must target /oauth/authorize on the configured IdP."""
    from urllib.parse import parse_qs, urlparse

    r = _client(monkeypatch, mode).get("/auth/start")
    assert r.status_code == 302

    parsed = urlparse(r.headers["location"])
    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.example.com"
    assert parsed.path == "/oauth/authorize"

    qs = parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["inferiallm-dashboard"]
    assert qs["redirect_uri"] == ["https://app/auth/callback"]
    assert qs["code_challenge_method"] == ["S256"]
    assert "code_challenge" in qs
    assert "state" in qs


# ---------------------------------------------------------------------------
# Absent oauth fields still 503 even for external modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["oidc", "inferiaauth"])
def test_auth_start_503_when_client_id_missing(monkeypatch, mode):
    """503 when oauth_client_id is blank, regardless of mode."""
    monkeypatch.setattr(orouter.settings, "auth_provider", mode, raising=False)
    monkeypatch.setattr(orouter.settings, "oauth_client_id", "", raising=False)
    monkeypatch.setattr(orouter.settings, "oauth_redirect_uri", "https://app/auth/callback", raising=False)
    monkeypatch.setattr(orouter.settings, "external_auth_url", "https://auth.example.com", raising=False)
    monkeypatch.setattr(orouter.settings, "app_namespace", "inferiallm", raising=False)
    orouter._oauth_client = None

    app = FastAPI()
    app.include_router(orouter.router)
    r = TestClient(app, follow_redirects=False).get("/auth/start")
    assert r.status_code == 503


@pytest.mark.parametrize("mode", ["oidc", "inferiaauth"])
def test_auth_start_503_when_redirect_uri_missing(monkeypatch, mode):
    """503 when oauth_redirect_uri is blank, regardless of mode."""
    monkeypatch.setattr(orouter.settings, "auth_provider", mode, raising=False)
    monkeypatch.setattr(orouter.settings, "oauth_client_id", "inferiallm-dashboard", raising=False)
    monkeypatch.setattr(orouter.settings, "oauth_redirect_uri", "", raising=False)
    monkeypatch.setattr(orouter.settings, "external_auth_url", "https://auth.example.com", raising=False)
    monkeypatch.setattr(orouter.settings, "app_namespace", "inferiallm", raising=False)
    orouter._oauth_client = None

    app = FastAPI()
    app.include_router(orouter.router)
    r = TestClient(app, follow_redirects=False).get("/auth/start")
    assert r.status_code == 503
