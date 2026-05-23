"""Tests for /auth/login gating in external mode (C6).

In external mode, /auth/login must reject everyone except the
superadmin: dashboard users go through /auth/start, and direct
password sign-in is reserved for break-glass.

Local mode is unchanged.
"""

from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _build_app(monkeypatch, *, auth_provider: str,
               external_auth_url: str = "https://auth.example.test",
               superadmin_email: str = "admin@inferia.test") -> FastAPI:
    """Build an isolated FastAPI app exposing only the auth router.

    Patches settings + rate limiter + auth_service for deterministic tests.
    """
    from inferia.services.api_gateway.config import settings as gw_settings

    monkeypatch.setattr(gw_settings, "auth_provider", auth_provider, raising=False)
    monkeypatch.setattr(gw_settings, "external_auth_url", external_auth_url, raising=False)
    monkeypatch.setattr(gw_settings, "superadmin_email", superadmin_email, raising=False)
    if auth_provider == "external":
        # Fill the rest of the required fields so other env-validators are happy.
        monkeypatch.setattr(gw_settings, "external_auth_issuer", external_auth_url, raising=False)
        monkeypatch.setattr(gw_settings, "oauth_client_id", "x", raising=False)
        monkeypatch.setattr(gw_settings, "oauth_redirect_uri",
                            "https://app.example.test/auth/callback", raising=False)

    # Disable rate limiting so the test isn't flaky on rapid retries.
    from inferia.common import rate_limit as rl
    monkeypatch.setattr(rl.login_rate_limiter, "is_allowed", lambda *a, **kw: (True, 0))

    app = FastAPI()
    from inferia.services.api_gateway.rbac.router import router as auth_router
    app.include_router(auth_router)
    return app


@pytest_asyncio.fixture
async def external_client(monkeypatch) -> AsyncIterator[AsyncClient]:
    app = _build_app(monkeypatch, auth_provider="external")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        yield c


@pytest_asyncio.fixture
async def local_client(monkeypatch) -> AsyncIterator[AsyncClient]:
    app = _build_app(monkeypatch, auth_provider="local")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        yield c


@pytest.mark.asyncio
async def test_external_non_superadmin_post_login_returns_403(external_client, monkeypatch):
    """Per C6: external mode + non-superadmin email must return 403 with brand-friendly text."""
    # Auth service should NEVER be reached (the gate fires before any DB or
    # password check).
    from inferia.services.api_gateway.rbac import router as r
    called = AsyncMock(return_value=None)
    monkeypatch.setattr(r.auth_service, "authenticate_user", called)

    resp = await external_client.post(
        "/auth/login",
        json={"username": "regular@example.test", "password": "anything"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["detail"] == "Direct password sign in is disabled. Use /auth/start."
    called.assert_not_called()


@pytest.mark.asyncio
async def test_external_superadmin_post_login_still_works(external_client, monkeypatch):
    """The superadmin must always be able to sign in directly (break-glass)."""
    from inferia.services.api_gateway.rbac import router as r
    from inferia.services.api_gateway.db.models import User as DBUser

    # Mock authenticate_user to return a non-None user for the superadmin.
    user = DBUser(
        id="admin-uuid",
        email="admin@inferia.test",
        password_hash="hashed",
        default_org_id="org-default",
        totp_enabled=False,
    )
    monkeypatch.setattr(
        r.auth_service, "authenticate_user", AsyncMock(return_value=user)
    )
    # Stub login() to return a canned AuthToken.
    from inferia.services.api_gateway.models import AuthToken
    monkeypatch.setattr(
        r.auth_service,
        "login",
        AsyncMock(
            return_value=AuthToken(
                access_token="atk-su", refresh_token="rtk-su",
                token_type="bearer", expires_in=900,
            )
        ),
    )

    resp = await external_client.post(
        "/auth/login",
        json={"username": "admin@inferia.test", "password": "p@ss"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] == "atk-su"


@pytest.mark.asyncio
async def test_local_mode_any_user_login_path_unaffected(local_client, monkeypatch):
    """In local mode, the new gate must not trigger — auth_service flows normally."""
    from inferia.services.api_gateway.rbac import router as r
    from inferia.services.api_gateway.db.models import User as DBUser
    from inferia.services.api_gateway.models import AuthToken

    user = DBUser(
        id="u-1",
        email="anyone@example.test",
        password_hash="hashed",
        default_org_id="org-default",
        totp_enabled=False,
    )
    monkeypatch.setattr(
        r.auth_service, "authenticate_user", AsyncMock(return_value=user)
    )
    monkeypatch.setattr(
        r.auth_service,
        "login",
        AsyncMock(
            return_value=AuthToken(
                access_token="atk", refresh_token="rtk",
                token_type="bearer", expires_in=900,
            )
        ),
    )

    resp = await local_client.post(
        "/auth/login",
        json={"username": "anyone@example.test", "password": "p@ss"},
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"] == "atk"


@pytest.mark.asyncio
async def test_external_with_empty_external_auth_url_does_not_gate(monkeypatch):
    """If AUTH_PROVIDER=external but external_auth_url is unset (misconfig),
    the gate is INACTIVE — local credentials still flow through so the
    operator can recover."""
    # We must set external_auth_url to empty before model_validator runs.
    # Build via monkeypatch after construction.
    app = _build_app(monkeypatch, auth_provider="external")
    from inferia.services.api_gateway.config import settings as gw_settings
    monkeypatch.setattr(gw_settings, "external_auth_url", "", raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://gw.example.test") as c:
        # Authenticate_user fails (no such user) — we expect 401, not 403.
        from inferia.services.api_gateway.rbac import router as r
        monkeypatch.setattr(
            r.auth_service, "authenticate_user", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            r.auth_service, "log_failed_login", AsyncMock(return_value=None)
        )
        resp = await c.post(
            "/auth/login",
            json={"username": "bob@example.test", "password": "x"},
        )
    # Because the gate is bypassed (external_auth_url is empty) and there's
    # no external_login path either (also empty), the local auth fall-through
    # kicks in. With authenticate_user returning None we get 401.
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_external_case_insensitive_superadmin_match_currently_strict(
    external_client, monkeypatch
):
    """Documents current behaviour: superadmin match is case-sensitive.

    If someone signs in with a differently-cased version of the superadmin
    email under external mode they are gated. We keep this test to lock
    behaviour; a future enhancement could lowercase both sides.
    """
    from inferia.services.api_gateway.rbac import router as r
    monkeypatch.setattr(
        r.auth_service, "authenticate_user", AsyncMock(return_value=None)
    )
    resp = await external_client.post(
        "/auth/login",
        json={"username": "ADMIN@inferia.test", "password": "p"},
    )
    assert resp.status_code == 403
