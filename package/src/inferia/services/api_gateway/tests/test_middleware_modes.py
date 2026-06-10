"""Tests for the 3-way auth-mode branching in auth middleware.

Tests _resolve_oidc_token and _resolve_external_token directly via stubbed
verifier + fake db + stubbed get_or_create_shadow_user, plus a branch-selection
test for auth_middleware.

Run with --noconftest to avoid the shared-conftest jwt fixture conflict:
  pytest package/.../tests/test_middleware_modes.py --noconftest
"""

import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import inferia.services.api_gateway.rbac.middleware as mw
from inferia.services.api_gateway.rbac.jwks_verifier import JWKSVerifyError
from inferia.services.api_gateway.models import UserContext


class _FakeUser:
    """Minimal shadow-user stand-in (mirrors DB User shape used by middleware)."""

    id = "u1"
    email = "a@b.com"


def _make_verifier(claims: dict):
    """Return a simple namespace whose verify_sync always returns `claims`."""
    return types.SimpleNamespace(verify_sync=lambda t: claims)


def _make_failing_verifier():
    """Return a verifier whose verify_sync raises JWKSVerifyError."""
    v = MagicMock()
    v.verify_sync.side_effect = JWKSVerifyError("nope")
    return v


# ---------------------------------------------------------------------------
# _resolve_oidc_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oidc_token_authenticated_is_admin(monkeypatch):
    """A valid OIDC token produces a UserContext with role=admin + all catalog perms."""
    monkeypatch.setattr(
        mw,
        "_get_verifier",
        lambda: _make_verifier(
            {"sub": "user:ext1", "email": "a@b.com", "org_id": "org9"}
        ),
    )
    monkeypatch.setattr(
        mw,
        "get_or_create_shadow_user",
        AsyncMock(return_value=(_FakeUser(), "org9", ["admin"])),
    )
    # Reset singleton so the patched _get_verifier is picked up
    monkeypatch.setattr(mw, "_verifier", None)

    ctx = await mw._resolve_oidc_token(MagicMock(), "tok")

    assert ctx.roles == ["admin"]
    assert "inferiallm:deployment:write" in ctx.permissions
    assert "inferiallm:model:read" in ctx.permissions
    assert ctx.org_id == "org9"
    assert ctx.user_id == "u1"
    assert ctx.email == "a@b.com"


@pytest.mark.asyncio
async def test_oidc_token_admin_has_all_catalog_perms(monkeypatch):
    """Admin permissions granted by OIDC path match the full catalog admin role."""
    from inferia.services.api_gateway.rbac.catalog import CATALOG

    monkeypatch.setattr(
        mw,
        "_get_verifier",
        lambda: _make_verifier({"sub": "ext2", "email": "b@c.com", "org_id": "o2"}),
    )
    monkeypatch.setattr(
        mw,
        "get_or_create_shadow_user",
        AsyncMock(return_value=(_FakeUser(), "o2", [])),
    )
    monkeypatch.setattr(mw, "_verifier", None)

    ctx = await mw._resolve_oidc_token(MagicMock(), "tok")

    # Catalog admin perms, expanded with their local-vocabulary equivalents.
    from inferia.services.api_gateway.rbac.permissions import expand_catalog_permissions

    expected = [p for r in CATALOG.roles if r.name == "admin" for p in r.permissions]
    assert set(ctx.permissions) == set(expand_catalog_permissions(expected))
    # The full catalog set is still present.
    assert set(expected) <= set(ctx.permissions)


@pytest.mark.asyncio
async def test_oidc_token_org_id_from_org_ids_fallback(monkeypatch):
    """When org_id claim absent, first entry of org_ids[] is used."""
    monkeypatch.setattr(
        mw,
        "_get_verifier",
        lambda: _make_verifier(
            {
                "sub": "ext3",
                "email": "c@d.com",
                "org_ids": ["org-first", "org-second"],
            }
        ),
    )
    monkeypatch.setattr(
        mw,
        "get_or_create_shadow_user",
        AsyncMock(return_value=(_FakeUser(), "local-org", [])),
    )
    monkeypatch.setattr(mw, "_verifier", None)

    ctx = await mw._resolve_oidc_token(MagicMock(), "tok")

    assert ctx.org_id == "org-first"


@pytest.mark.asyncio
async def test_oidc_token_no_org_claim_gives_none_org(monkeypatch):
    """When neither org_id nor org_ids present, org_id is None."""
    monkeypatch.setattr(
        mw,
        "_get_verifier",
        lambda: _make_verifier({"sub": "ext4", "email": "d@e.com"}),
    )
    monkeypatch.setattr(
        mw,
        "get_or_create_shadow_user",
        AsyncMock(return_value=(_FakeUser(), None, [])),
    )
    monkeypatch.setattr(mw, "_verifier", None)

    ctx = await mw._resolve_oidc_token(MagicMock(), "tok")

    assert ctx.org_id is None


@pytest.mark.asyncio
async def test_oidc_token_invalid_raises_401(monkeypatch):
    """A JWKSVerifyError from _get_verifier surfaces as HTTPException(401)."""
    monkeypatch.setattr(mw, "_get_verifier", _make_failing_verifier)
    monkeypatch.setattr(mw, "_verifier", None)

    with pytest.raises(mw.HTTPException) as exc_info:
        await mw._resolve_oidc_token(MagicMock(), "tok")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid token"


@pytest.mark.asyncio
async def test_oidc_token_sub_colon_prefix_stripped(monkeypatch):
    """The 'user:' prefix in sub is stripped before passing as external_id."""
    shadow_mock = AsyncMock(return_value=(_FakeUser(), "org1", []))
    monkeypatch.setattr(
        mw,
        "_get_verifier",
        lambda: _make_verifier(
            {"sub": "user:the-real-id", "email": "e@f.com", "org_id": "org1"}
        ),
    )
    monkeypatch.setattr(mw, "get_or_create_shadow_user", shadow_mock)
    monkeypatch.setattr(mw, "_verifier", None)

    await mw._resolve_oidc_token(MagicMock(), "tok")

    _, kwargs = shadow_mock.await_args
    assert kwargs["external_id"] == "the-real-id"


@pytest.mark.asyncio
async def test_oidc_token_sub_no_colon_passed_through(monkeypatch):
    """A bare sub (no colon prefix) is passed through unchanged."""
    shadow_mock = AsyncMock(return_value=(_FakeUser(), "org2", []))
    monkeypatch.setattr(
        mw,
        "_get_verifier",
        lambda: _make_verifier(
            {"sub": "bare-id", "email": "f@g.com", "org_id": "org2"}
        ),
    )
    monkeypatch.setattr(mw, "get_or_create_shadow_user", shadow_mock)
    monkeypatch.setattr(mw, "_verifier", None)

    await mw._resolve_oidc_token(MagicMock(), "tok")

    _, kwargs = shadow_mock.await_args
    assert kwargs["external_id"] == "bare-id"


# ---------------------------------------------------------------------------
# _resolve_external_token (inferiaauth path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inferiaauth_token_uses_claims(monkeypatch):
    """InferiaAuth token: roles + permissions come straight from JWT claims."""
    monkeypatch.setattr(
        mw,
        "_get_verifier",
        lambda: _make_verifier(
            {
                "sub": "user:ext1",
                "email": "a@b.com",
                "roles": ["viewer"],
                "permissions": ["inferiallm:model:read"],
                "org_id": "o1",
            }
        ),
    )
    monkeypatch.setattr(
        mw,
        "get_or_create_shadow_user",
        AsyncMock(return_value=(_FakeUser(), "o1", ["viewer"])),
    )
    monkeypatch.setattr(mw, "_verifier", None)

    ctx = await mw._resolve_external_token(MagicMock(), "tok")

    # Claim permissions are kept and expanded to local equivalents.
    assert set(ctx.permissions) == {
        "inferiallm:model:read",
        "model:list",
        "model:access",
    }
    assert ctx.roles == ["viewer"]
    assert ctx.org_id == "o1"


@pytest.mark.asyncio
async def test_inferiaauth_token_invalid_raises_401(monkeypatch):
    """JWKSVerifyError from _resolve_external_token surfaces as HTTPException(401)."""
    monkeypatch.setattr(mw, "_get_verifier", _make_failing_verifier)
    monkeypatch.setattr(mw, "_verifier", None)

    with pytest.raises(mw.HTTPException) as exc_info:
        await mw._resolve_external_token(MagicMock(), "tok")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_inferiaauth_missing_roles_defaults_to_empty(monkeypatch):
    """When roles/permissions claims are absent, lists default to empty."""
    monkeypatch.setattr(
        mw,
        "_get_verifier",
        lambda: _make_verifier(
            {"sub": "ext5", "email": "g@h.com", "org_id": "o5"}
        ),
    )
    monkeypatch.setattr(
        mw,
        "get_or_create_shadow_user",
        AsyncMock(return_value=(_FakeUser(), "o5", [])),
    )
    monkeypatch.setattr(mw, "_verifier", None)

    ctx = await mw._resolve_external_token(MagicMock(), "tok")

    assert ctx.roles == []
    assert ctx.permissions == []


@pytest.mark.asyncio
async def test_inferiaauth_org_id_falls_back_to_org_ids(monkeypatch):
    """When org_id absent, first entry of org_ids[] used."""
    monkeypatch.setattr(
        mw,
        "_get_verifier",
        lambda: _make_verifier(
            {
                "sub": "ext6",
                "email": "h@i.com",
                "org_ids": ["o-first", "o-second"],
            }
        ),
    )
    monkeypatch.setattr(
        mw,
        "get_or_create_shadow_user",
        AsyncMock(return_value=(_FakeUser(), None, [])),
    )
    monkeypatch.setattr(mw, "_verifier", None)

    ctx = await mw._resolve_external_token(MagicMock(), "tok")

    assert ctx.org_id == "o-first"


# ---------------------------------------------------------------------------
# auth_middleware branch selection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_branch_inferiaauth_calls_external_resolver(monkeypatch):
    """With mode=inferiaauth, after local-token failure the external resolver runs."""
    from fastapi import HTTPException as FastAPIHTTPException

    monkeypatch.setattr(mw.settings, "auth_provider", "inferiaauth", raising=False)

    local_fail = AsyncMock(side_effect=FastAPIHTTPException(status_code=401, detail="bad"))
    external_ok = AsyncMock(
        return_value=UserContext(
            user_id="u2", username="x@y.com", email="x@y.com",
            roles=["admin"], permissions=[], org_id="o2",
            quota_limit=10000, quota_used=0,
        )
    )
    monkeypatch.setattr(mw, "_resolve_local_token", local_fail)
    monkeypatch.setattr(mw, "_resolve_external_token", external_ok)

    fake_db = AsyncMock()
    fake_db.__aenter__ = AsyncMock(return_value=fake_db)
    fake_db.__aexit__ = AsyncMock(return_value=None)

    with patch.object(mw, "AsyncSessionLocal", return_value=fake_db):
        # Build a minimal request with a cache-busting token
        request = MagicMock()
        request.headers.get = MagicMock(
            side_effect=lambda h, d="": {
                "upgrade": "",
                "connection": "",
                "Authorization": "Bearer fresh-token-inferiaauth",
            }.get(h, d)
        )
        request.url.path = "/some/protected"
        request.method = "GET"
        request.state = MagicMock()
        # Ensure cache miss
        mw._auth_cache.clear()

        async def _call_next(req):
            return MagicMock(status_code=200)

        await mw.auth_middleware(request, _call_next)

    local_fail.assert_awaited_once()
    external_ok.assert_awaited_once()


@pytest.mark.asyncio
async def test_branch_oidc_calls_oidc_resolver(monkeypatch):
    """With mode=oidc, after local-token failure the OIDC resolver runs."""
    from fastapi import HTTPException as FastAPIHTTPException

    monkeypatch.setattr(mw.settings, "auth_provider", "oidc", raising=False)

    local_fail = AsyncMock(side_effect=FastAPIHTTPException(status_code=401, detail="bad"))
    oidc_ok = AsyncMock(
        return_value=UserContext(
            user_id="u3", username="y@z.com", email="y@z.com",
            roles=["admin"], permissions=[], org_id="o3",
            quota_limit=10000, quota_used=0,
        )
    )
    monkeypatch.setattr(mw, "_resolve_local_token", local_fail)
    monkeypatch.setattr(mw, "_resolve_oidc_token", oidc_ok)

    fake_db = AsyncMock()
    fake_db.__aenter__ = AsyncMock(return_value=fake_db)
    fake_db.__aexit__ = AsyncMock(return_value=None)

    with patch.object(mw, "AsyncSessionLocal", return_value=fake_db):
        request = MagicMock()
        request.headers.get = MagicMock(
            side_effect=lambda h, d="": {
                "upgrade": "",
                "connection": "",
                "Authorization": "Bearer fresh-token-oidc",
            }.get(h, d)
        )
        request.url.path = "/api/protected"
        request.method = "POST"
        request.state = MagicMock()
        mw._auth_cache.clear()

        async def _call_next(req):
            return MagicMock(status_code=200)

        await mw.auth_middleware(request, _call_next)

    local_fail.assert_awaited_once()
    oidc_ok.assert_awaited_once()


@pytest.mark.asyncio
async def test_branch_local_only_calls_local_resolver(monkeypatch):
    """With mode=local, only _resolve_local_token is called (no external)."""
    monkeypatch.setattr(mw.settings, "auth_provider", "local", raising=False)

    local_ok = AsyncMock(
        return_value=UserContext(
            user_id="u4", username="z@a.com", email="z@a.com",
            roles=["member"], permissions=["inferiallm:model:read"], org_id="o4",
            quota_limit=10000, quota_used=0,
        )
    )
    external_mock = AsyncMock()
    oidc_mock = AsyncMock()
    monkeypatch.setattr(mw, "_resolve_local_token", local_ok)
    monkeypatch.setattr(mw, "_resolve_external_token", external_mock)
    monkeypatch.setattr(mw, "_resolve_oidc_token", oidc_mock)

    fake_db = AsyncMock()
    fake_db.__aenter__ = AsyncMock(return_value=fake_db)
    fake_db.__aexit__ = AsyncMock(return_value=None)

    with patch.object(mw, "AsyncSessionLocal", return_value=fake_db):
        request = MagicMock()
        request.headers.get = MagicMock(
            side_effect=lambda h, d="": {
                "upgrade": "",
                "connection": "",
                "Authorization": "Bearer fresh-token-local",
            }.get(h, d)
        )
        request.url.path = "/api/data"
        request.method = "GET"
        request.state = MagicMock()
        mw._auth_cache.clear()

        async def _call_next(req):
            return MagicMock(status_code=200)

        await mw.auth_middleware(request, _call_next)

    local_ok.assert_awaited_once()
    external_mock.assert_not_awaited()
    oidc_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_branch_inferiaauth_superadmin_local_token_succeeds(monkeypatch):
    """With mode=inferiaauth, if local token succeeds the external path is NOT called."""
    monkeypatch.setattr(mw.settings, "auth_provider", "inferiaauth", raising=False)

    local_ok = AsyncMock(
        return_value=UserContext(
            user_id="superadmin", username="sa@inferia.local", email="sa@inferia.local",
            roles=["admin"], permissions=[], org_id=None,
            quota_limit=10000, quota_used=0,
        )
    )
    external_mock = AsyncMock()
    monkeypatch.setattr(mw, "_resolve_local_token", local_ok)
    monkeypatch.setattr(mw, "_resolve_external_token", external_mock)

    fake_db = AsyncMock()
    fake_db.__aenter__ = AsyncMock(return_value=fake_db)
    fake_db.__aexit__ = AsyncMock(return_value=None)

    with patch.object(mw, "AsyncSessionLocal", return_value=fake_db):
        request = MagicMock()
        request.headers.get = MagicMock(
            side_effect=lambda h, d="": {
                "upgrade": "",
                "connection": "",
                "Authorization": "Bearer fresh-token-superadmin",
            }.get(h, d)
        )
        request.url.path = "/api/admin"
        request.method = "GET"
        request.state = MagicMock()
        mw._auth_cache.clear()

        async def _call_next(req):
            return MagicMock(status_code=200)

        await mw.auth_middleware(request, _call_next)

    local_ok.assert_awaited_once()
    external_mock.assert_not_awaited()
