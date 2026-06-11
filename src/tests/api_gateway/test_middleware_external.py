"""Tests for the JWKS-based external-token path in auth middleware.

Verifies that _resolve_external_token (C5):
  1. Verifies tokens via JWKSVerifier (NOT inferia-auth introspect)
  2. Reads roles & permissions directly from JWT claims, NOT local DB
  3. Provisions shadow users on first sight, reuses on subsequent
  4. Maps JWKSVerifyError → HTTPException(401)
  5. Preserves the superadmin local-token path (untouched)
"""

import base64
import time
from typing import Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from api_gateway.db.models import User as DBUser
from api_gateway.models import UserContext


def _jwk_from_public(priv: Ed25519PrivateKey, kid: str = "test-key") -> dict:
    raw_pub = priv.public_key().public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw
    )
    x = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode()
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "kid": kid,
        "use": "sig",
        "alg": "EdDSA",
        "x": x,
    }


@pytest.fixture
def keypair() -> Tuple[Ed25519PrivateKey, dict]:
    priv = Ed25519PrivateKey.generate()
    jwks = {"keys": [_jwk_from_public(priv)]}
    return priv, jwks


def _sign(priv: Ed25519PrivateKey, claims: dict) -> str:
    pem = priv.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode()
    return pyjwt.encode(claims, pem, algorithm="EdDSA", headers={"kid": "test-key"})


def _default_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "iss": "https://auth.local",
        "aud": "inferiallm",
        "sub": "user:01HX-uuid",
        "exp": now + 60,
        "iat": now,
        "type": "access",
        "email": "ext@example.test",
        "roles": ["admin", "auditor"],
        "permissions": [
            "inferiallm:deployment:read",
            "inferiallm:audit:read",
        ],
        "org_id": "org-from-claims",
        "org_ids": ["org-from-claims"],
        "scope": "openid profile email inferiallm",
    }
    claims.update(overrides)
    return claims


def _make_user(email="ext@example.test", id_="user-1") -> DBUser:
    return DBUser(
        id=id_,
        email=email,
        password_hash="!external!",
        default_org_id="org-local",
        totp_enabled=False,
    )


@pytest.fixture(autouse=True)
def patch_settings(httpserver, monkeypatch):
    """Wire settings to a fake JWKS endpoint + reset module-level singleton."""
    from api_gateway.config import settings
    from api_gateway.rbac import middleware as mw

    base = httpserver.url_for("")
    monkeypatch.setattr(settings, "auth_provider", "external", raising=False)
    monkeypatch.setattr(settings, "external_auth_url", base.rstrip("/"), raising=False)
    monkeypatch.setattr(
        settings, "external_auth_issuer", "https://auth.local", raising=False
    )
    monkeypatch.setattr(settings, "app_namespace", "inferiallm", raising=False)
    monkeypatch.setattr(settings, "oauth_jwks_cache_ttl_seconds", 3600, raising=False)
    # Reset the lazy verifier singleton in the middleware module.
    mw._verifier = None
    yield
    mw._verifier = None


@pytest.mark.asyncio
async def test_valid_token_uses_claim_roles_not_db(httpserver, keypair):
    from api_gateway.rbac import middleware as mw

    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)

    token = _sign(priv, _default_claims())

    # Patch shadow user lookup to return a known user but with roles that
    # would DIFFER from the claim if the middleware read them from DB.
    fake_user = _make_user()
    with patch(
        "api_gateway.rbac.middleware.get_or_create_shadow_user",
        AsyncMock(return_value=(fake_user, "org-local", ["member"])),
    ):
        db = AsyncMock()
        ctx = await mw._resolve_external_token(db, token)

    assert isinstance(ctx, UserContext)
    assert ctx.email == "ext@example.test"
    # Claims win, not the DB-derived 'member' role.
    assert ctx.roles == ["admin", "auditor"]
    # Catalog keys are kept AND expanded to their local equivalents.
    assert set(ctx.permissions) == {
        "inferiallm:deployment:read",
        "deployment:list",
        "inferiallm:audit:read",
        "audit_log:list",
    }
    # org_id comes from claims.
    assert ctx.org_id == "org-from-claims"


@pytest.mark.asyncio
async def test_catalog_org_read_grants_local_organization_view(httpserver, keypair):
    """The dashboard gates /dashboard on the LOCAL 'organization:view' — a token
    carrying only catalog keys (inferiallm:org:read) must expand to include it,
    or every SaaS user is locked out of the dashboard."""
    from api_gateway.rbac import middleware as mw

    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)

    token = _sign(
        priv,
        _default_claims(permissions=["inferiallm:org:read", "inferiallm:org:write"]),
    )

    fake_user = _make_user()
    with patch(
        "api_gateway.rbac.middleware.get_or_create_shadow_user",
        AsyncMock(return_value=(fake_user, "org-local", ["member"])),
    ):
        db = AsyncMock()
        ctx = await mw._resolve_external_token(db, token)

    assert "organization:view" in ctx.permissions
    assert "organization:update" in ctx.permissions
    # Originals are preserved.
    assert "inferiallm:org:read" in ctx.permissions
    assert "inferiallm:org:write" in ctx.permissions


@pytest.mark.asyncio
async def test_external_token_provisions_shadow_org(httpserver, keypair):
    """The IdP org has no local row; resolution must call ensure_external_org
    with the org id, the shadow user's id, and the caller's bearer token."""
    from api_gateway.rbac import middleware as mw

    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)

    token = _sign(priv, _default_claims())

    fake_user = _make_user()
    ensure_mock = AsyncMock()
    with patch(
        "api_gateway.rbac.middleware.get_or_create_shadow_user",
        AsyncMock(return_value=(fake_user, "org-local", ["member"])),
    ), patch(
        "api_gateway.rbac.middleware.ensure_external_org",
        ensure_mock,
    ):
        db = AsyncMock()
        ctx = await mw._resolve_external_token(db, token)

    ensure_mock.assert_awaited_once_with(
        db, "org-from-claims", user_id=fake_user.id, bearer_token=token
    )
    assert ctx.org_id == "org-from-claims"


@pytest.mark.asyncio
async def test_expired_token_raises_401(httpserver, keypair):
    from fastapi import HTTPException
    from api_gateway.rbac import middleware as mw

    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    token = _sign(
        priv,
        _default_claims(exp=int(time.time()) - 300, iat=int(time.time()) - 600),
    )
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await mw._resolve_external_token(db, token)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_wrong_audience_raises_401(httpserver, keypair):
    from fastapi import HTTPException
    from api_gateway.rbac import middleware as mw

    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    token = _sign(priv, _default_claims(aud="something-else"))
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await mw._resolve_external_token(db, token)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_new_email_provisions_shadow_user(httpserver, keypair):
    from api_gateway.rbac import middleware as mw

    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    token = _sign(priv, _default_claims(email="newcomer@example.test"))

    fake_user = _make_user(email="newcomer@example.test", id_="user-new")
    shadow_mock = AsyncMock(return_value=(fake_user, "org-local", ["member"]))
    with patch(
        "api_gateway.rbac.middleware.get_or_create_shadow_user",
        shadow_mock,
    ):
        db = AsyncMock()
        await mw._resolve_external_token(db, token)

    shadow_mock.assert_awaited_once()
    # The call args include email + external_id derived from sub.
    args, kwargs = shadow_mock.await_args
    assert kwargs["email"] == "newcomer@example.test"
    assert kwargs["external_id"] == "01HX-uuid"  # "user:" prefix stripped


@pytest.mark.asyncio
async def test_existing_user_reused_no_duplicate(httpserver, keypair):
    """Existing shadow user with the same email is reused (verified by
    asserting the same User instance flows out)."""
    from api_gateway.rbac import middleware as mw

    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)

    existing = _make_user(email="ext@example.test", id_="user-existing-id")
    shadow_mock = AsyncMock(return_value=(existing, "org-local", ["member"]))
    with patch(
        "api_gateway.rbac.middleware.get_or_create_shadow_user",
        shadow_mock,
    ):
        db = AsyncMock()
        ctx1 = await mw._resolve_external_token(db, _sign(priv, _default_claims()))
        ctx2 = await mw._resolve_external_token(db, _sign(priv, _default_claims()))

    assert ctx1.user_id == "user-existing-id"
    assert ctx2.user_id == "user-existing-id"
    # Shadow user provisioning ran twice (once per request, idempotent).
    assert shadow_mock.await_count == 2


@pytest.mark.asyncio
async def test_org_id_falls_back_to_org_ids_list(httpserver, keypair):
    from api_gateway.rbac import middleware as mw

    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)

    claims = _default_claims()
    claims.pop("org_id", None)
    claims["org_ids"] = ["org-aaa", "org-bbb"]
    token = _sign(priv, claims)

    with patch(
        "api_gateway.rbac.middleware.get_or_create_shadow_user",
        AsyncMock(return_value=(_make_user(), "org-local", ["member"])),
    ):
        db = AsyncMock()
        ctx = await mw._resolve_external_token(db, token)
    assert ctx.org_id == "org-aaa"


@pytest.mark.asyncio
async def test_missing_roles_claim_defaults_to_empty(httpserver, keypair):
    from api_gateway.rbac import middleware as mw

    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)

    claims = _default_claims()
    claims.pop("roles", None)
    claims.pop("permissions", None)
    token = _sign(priv, claims)

    with patch(
        "api_gateway.rbac.middleware.get_or_create_shadow_user",
        AsyncMock(return_value=(_make_user(), "org-local", ["member"])),
    ):
        db = AsyncMock()
        ctx = await mw._resolve_external_token(db, token)
    assert ctx.roles == []
    assert ctx.permissions == []


@pytest.mark.asyncio
async def test_sub_without_colon_prefix_passed_through(httpserver, keypair):
    """Tokens with a bare sub (no 'user:' prefix) still work."""
    from api_gateway.rbac import middleware as mw

    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)

    token = _sign(priv, _default_claims(sub="01HX-bare"))
    shadow_mock = AsyncMock(return_value=(_make_user(), "org-local", ["member"]))
    with patch(
        "api_gateway.rbac.middleware.get_or_create_shadow_user",
        shadow_mock,
    ):
        db = AsyncMock()
        await mw._resolve_external_token(db, token)

    args, kwargs = shadow_mock.await_args
    assert kwargs["external_id"] == "01HX-bare"


@pytest.mark.asyncio
async def test_token_with_huge_length_rejected_401(httpserver, keypair):
    """JWKSVerifier's token-length cap must surface as 401 in middleware."""
    from fastapi import HTTPException
    from api_gateway.rbac import middleware as mw

    _, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)

    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await mw._resolve_external_token(db, "a" * 9000)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verifier_singleton_reuses_jwks_cache(httpserver, keypair):
    """The middleware should not re-instantiate JWKSVerifier per request."""
    from api_gateway.rbac import middleware as mw

    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)

    with patch(
        "api_gateway.rbac.middleware.get_or_create_shadow_user",
        AsyncMock(return_value=(_make_user(), "org-local", ["member"])),
    ):
        db = AsyncMock()
        for _ in range(3):
            await mw._resolve_external_token(db, _sign(priv, _default_claims()))

    jwks_hits = [r for r, _ in httpserver.log if r.path == "/.well-known/jwks.json"]
    assert len(jwks_hits) == 1


@pytest.mark.asyncio
async def test_wrong_issuer_raises_401(httpserver, keypair):
    from fastapi import HTTPException
    from api_gateway.rbac import middleware as mw

    priv, jwks = keypair
    httpserver.expect_request("/.well-known/jwks.json").respond_with_json(jwks)
    token = _sign(priv, _default_claims(iss="https://evil.example.test"))
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await mw._resolve_external_token(db, token)
    assert exc.value.status_code == 401
