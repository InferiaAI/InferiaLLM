"""Tests for OIDC group→role mapping in _resolve_oidc_token.

Covers:
  - empty oidc_role_map (default) → admin with full catalog perms (unchanged behavior)
  - group match → correct role assigned
  - viewer role → only viewer perms, no admin-only keys
  - no group match → oidc_default_role
  - custom oidc_groups_claim is honored
  - single-string groups value is coerced to list
  - unknown role in map → fail-closed (empty permissions)
  - multiple groups, first match wins

Run with --noconftest to avoid the shared-conftest jwt fixture conflict:
  PYTHONNOUSERSITE=1 PYTHONPATH=src \
    pytest src/tests/api_gateway/test_oidc_role_mapping.py --noconftest
"""

import types
from unittest.mock import AsyncMock, MagicMock

import pytest

import services.api_gateway.rbac.middleware as mw
from services.api_gateway.rbac.jwks_verifier import JWKSVerifyError
from services.api_gateway.rbac.catalog import CATALOG


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeUser:
    """Minimal shadow-user stand-in."""

    id = "u1"
    email = "a@b.com"


def _make_verifier(claims: dict):
    """Return a simple namespace whose verify_sync always returns `claims`."""
    return types.SimpleNamespace(verify_sync=lambda t: claims)


def _admin_perms() -> list[str]:
    """Return the full admin permission list from the catalog."""
    return [p for r in CATALOG.roles if r.name == "admin" for p in r.permissions]


def _viewer_perms() -> list[str]:
    """Return the viewer permission list from the catalog."""
    return [p for r in CATALOG.roles if r.name == "viewer" for p in r.permissions]


def _member_perms() -> list[str]:
    """Return the member permission list from the catalog."""
    return [p for r in CATALOG.roles if r.name == "member" for p in r.permissions]


def _make_settings_stub(
    oidc_role_map: dict,
    oidc_groups_claim: str = "groups",
    oidc_default_role: str = "viewer",
):
    """Create a minimal settings stub with only the OIDC-mapping fields."""
    stub = MagicMock()
    stub.oidc_role_map = oidc_role_map
    stub.oidc_groups_claim = oidc_groups_claim
    stub.oidc_default_role = oidc_default_role
    return stub


def _shadow_mock():
    """Return a patched get_or_create_shadow_user that yields _FakeUser."""
    return AsyncMock(return_value=(_FakeUser(), "org1", []))


# ---------------------------------------------------------------------------
# Default: empty oidc_role_map → admin (unchanged interim behavior)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_role_map_gives_admin(monkeypatch):
    """When oidc_role_map is empty, any authenticated user gets role=admin."""
    monkeypatch.setattr(
        mw, "_get_verifier",
        lambda: _make_verifier({"sub": "ext1", "email": "a@b.com", "org_id": "o1",
                                "groups": ["some-group"]}),
    )
    monkeypatch.setattr(mw, "get_or_create_shadow_user", _shadow_mock())
    monkeypatch.setattr(mw, "_verifier", None)
    monkeypatch.setattr(mw, "settings", _make_settings_stub(oidc_role_map={}))

    ctx = await mw._resolve_oidc_token(MagicMock(), "tok")

    assert ctx.roles == ["admin"]
    assert "inferiallm:deployment:write" in ctx.permissions
    assert set(ctx.permissions) == set(_admin_perms())


@pytest.mark.asyncio
async def test_empty_role_map_no_groups_claim_still_admin(monkeypatch):
    """Empty map + no groups in token still resolves to admin (default unchanged)."""
    monkeypatch.setattr(
        mw, "_get_verifier",
        lambda: _make_verifier({"sub": "ext2", "email": "b@c.com", "org_id": "o2"}),
    )
    monkeypatch.setattr(mw, "get_or_create_shadow_user", _shadow_mock())
    monkeypatch.setattr(mw, "_verifier", None)
    monkeypatch.setattr(mw, "settings", _make_settings_stub(oidc_role_map={}))

    ctx = await mw._resolve_oidc_token(MagicMock(), "tok")

    assert ctx.roles == ["admin"]
    assert "inferiallm:deployment:write" in ctx.permissions


# ---------------------------------------------------------------------------
# Group match → admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_match_admin(monkeypatch):
    """oidc_role_map={"llm-admins":"admin"} + groups=["llm-admins"] → role=admin."""
    monkeypatch.setattr(
        mw, "_get_verifier",
        lambda: _make_verifier(
            {"sub": "ext3", "email": "c@d.com", "org_id": "o3",
             "groups": ["llm-admins"]}
        ),
    )
    monkeypatch.setattr(mw, "get_or_create_shadow_user", _shadow_mock())
    monkeypatch.setattr(mw, "_verifier", None)
    monkeypatch.setattr(
        mw, "settings",
        _make_settings_stub(oidc_role_map={"llm-admins": "admin"}),
    )

    ctx = await mw._resolve_oidc_token(MagicMock(), "tok")

    assert ctx.roles == ["admin"]
    assert set(ctx.permissions) == set(_admin_perms())


# ---------------------------------------------------------------------------
# Group match → viewer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_match_viewer(monkeypatch):
    """oidc_role_map={"llm-users":"viewer"} + groups=["llm-users"] → role=viewer."""
    monkeypatch.setattr(
        mw, "_get_verifier",
        lambda: _make_verifier(
            {"sub": "ext4", "email": "d@e.com", "org_id": "o4",
             "groups": ["llm-users"]}
        ),
    )
    monkeypatch.setattr(mw, "get_or_create_shadow_user", _shadow_mock())
    monkeypatch.setattr(mw, "_verifier", None)
    monkeypatch.setattr(
        mw, "settings",
        _make_settings_stub(oidc_role_map={"llm-users": "viewer"}),
    )

    ctx = await mw._resolve_oidc_token(MagicMock(), "tok")

    assert ctx.roles == ["viewer"]
    # Must include the 3 viewer read keys
    expected_viewer = set(_viewer_perms())
    assert set(ctx.permissions) == expected_viewer
    # Must NOT include admin-only write key
    assert "inferiallm:deployment:write" not in ctx.permissions
    assert "inferiallm:model:read" in ctx.permissions


# ---------------------------------------------------------------------------
# No group match → oidc_default_role
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_group_match_falls_back_to_default_role(monkeypatch):
    """When no group matches the map, oidc_default_role is used."""
    monkeypatch.setattr(
        mw, "_get_verifier",
        lambda: _make_verifier(
            {"sub": "ext5", "email": "e@f.com", "org_id": "o5",
             "groups": ["other-group", "unrelated"]}
        ),
    )
    monkeypatch.setattr(mw, "get_or_create_shadow_user", _shadow_mock())
    monkeypatch.setattr(mw, "_verifier", None)
    monkeypatch.setattr(
        mw, "settings",
        _make_settings_stub(
            oidc_role_map={"x": "admin"},
            oidc_default_role="viewer",
        ),
    )

    ctx = await mw._resolve_oidc_token(MagicMock(), "tok")

    assert ctx.roles == ["viewer"]
    assert set(ctx.permissions) == set(_viewer_perms())


@pytest.mark.asyncio
async def test_no_groups_claim_in_token_falls_back_to_default_role(monkeypatch):
    """When the groups claim is absent, oidc_default_role is used."""
    monkeypatch.setattr(
        mw, "_get_verifier",
        lambda: _make_verifier({"sub": "ext6", "email": "f@g.com", "org_id": "o6"}),
    )
    monkeypatch.setattr(mw, "get_or_create_shadow_user", _shadow_mock())
    monkeypatch.setattr(mw, "_verifier", None)
    monkeypatch.setattr(
        mw, "settings",
        _make_settings_stub(
            oidc_role_map={"llm-admins": "admin"},
            oidc_default_role="viewer",
        ),
    )

    ctx = await mw._resolve_oidc_token(MagicMock(), "tok")

    assert ctx.roles == ["viewer"]


# ---------------------------------------------------------------------------
# Custom oidc_groups_claim is honored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_groups_claim_is_honored(monkeypatch):
    """oidc_groups_claim='roles' reads groups from the 'roles' JWT claim."""
    monkeypatch.setattr(
        mw, "_get_verifier",
        lambda: _make_verifier(
            {"sub": "ext7", "email": "g@h.com", "org_id": "o7",
             "roles": ["llm-admins"],  # custom claim
             "groups": ["not-used"]}  # default claim should NOT be read
        ),
    )
    monkeypatch.setattr(mw, "get_or_create_shadow_user", _shadow_mock())
    monkeypatch.setattr(mw, "_verifier", None)
    monkeypatch.setattr(
        mw, "settings",
        _make_settings_stub(
            oidc_role_map={"llm-admins": "admin", "not-used": "viewer"},
            oidc_groups_claim="roles",
        ),
    )

    ctx = await mw._resolve_oidc_token(MagicMock(), "tok")

    # The "roles" claim has "llm-admins" → admin
    assert ctx.roles == ["admin"]
    assert set(ctx.permissions) == set(_admin_perms())


# ---------------------------------------------------------------------------
# Member role mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_match_member(monkeypatch):
    """Group mapped to 'member' yields member catalog permissions."""
    monkeypatch.setattr(
        mw, "_get_verifier",
        lambda: _make_verifier(
            {"sub": "ext8", "email": "h@i.com", "org_id": "o8",
             "groups": ["llm-members"]}
        ),
    )
    monkeypatch.setattr(mw, "get_or_create_shadow_user", _shadow_mock())
    monkeypatch.setattr(mw, "_verifier", None)
    monkeypatch.setattr(
        mw, "settings",
        _make_settings_stub(oidc_role_map={"llm-members": "member"}),
    )

    ctx = await mw._resolve_oidc_token(MagicMock(), "tok")

    assert ctx.roles == ["member"]
    assert set(ctx.permissions) == set(_member_perms())
    assert "inferiallm:deployment:write" not in ctx.permissions
    assert "inferiallm:deployment:read" in ctx.permissions


# ---------------------------------------------------------------------------
# Unknown role in map → fail-closed (empty permissions)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_role_in_map_gives_no_permissions(monkeypatch):
    """A role name that doesn't exist in the catalog yields empty permissions (fail-closed)."""
    monkeypatch.setattr(
        mw, "_get_verifier",
        lambda: _make_verifier(
            {"sub": "ext9", "email": "i@j.com", "org_id": "o9",
             "groups": ["mystery-group"]}
        ),
    )
    monkeypatch.setattr(mw, "get_or_create_shadow_user", _shadow_mock())
    monkeypatch.setattr(mw, "_verifier", None)
    monkeypatch.setattr(
        mw, "settings",
        _make_settings_stub(oidc_role_map={"mystery-group": "nonexistent-role"}),
    )

    ctx = await mw._resolve_oidc_token(MagicMock(), "tok")

    assert ctx.roles == ["nonexistent-role"]
    assert ctx.permissions == []


# ---------------------------------------------------------------------------
# Multiple groups, first match wins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_matching_group_wins(monkeypatch):
    """When multiple groups appear in the map, the first one (iteration order) wins."""
    monkeypatch.setattr(
        mw, "_get_verifier",
        lambda: _make_verifier(
            {"sub": "ext10", "email": "j@k.com", "org_id": "o10",
             "groups": ["llm-viewers", "llm-admins"]}
        ),
    )
    monkeypatch.setattr(mw, "get_or_create_shadow_user", _shadow_mock())
    monkeypatch.setattr(mw, "_verifier", None)
    monkeypatch.setattr(
        mw, "settings",
        _make_settings_stub(
            oidc_role_map={"llm-viewers": "viewer", "llm-admins": "admin"},
        ),
    )

    ctx = await mw._resolve_oidc_token(MagicMock(), "tok")

    # "llm-viewers" appears first in the groups list, so viewer wins
    assert ctx.roles == ["viewer"]


# ---------------------------------------------------------------------------
# Single string groups coerced to list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_string_groups_claim_is_coerced(monkeypatch):
    """If the groups claim is a bare string (not a list), it is treated as one-element list."""
    monkeypatch.setattr(
        mw, "_get_verifier",
        lambda: _make_verifier(
            {"sub": "ext11", "email": "k@l.com", "org_id": "o11",
             "groups": "llm-admins"}  # string, not list
        ),
    )
    monkeypatch.setattr(mw, "get_or_create_shadow_user", _shadow_mock())
    monkeypatch.setattr(mw, "_verifier", None)
    monkeypatch.setattr(
        mw, "settings",
        _make_settings_stub(oidc_role_map={"llm-admins": "admin"}),
    )

    ctx = await mw._resolve_oidc_token(MagicMock(), "tok")

    assert ctx.roles == ["admin"]
    assert set(ctx.permissions) == set(_admin_perms())


# ---------------------------------------------------------------------------
# _catalog_role_permissions helper unit tests
# ---------------------------------------------------------------------------


def test_catalog_role_permissions_admin():
    """_catalog_role_permissions('admin') returns the full admin perm list."""
    perms = mw._catalog_role_permissions("admin")
    assert set(perms) == set(_admin_perms())
    assert "inferiallm:deployment:write" in perms


def test_catalog_role_permissions_viewer():
    """_catalog_role_permissions('viewer') returns only the 3 viewer keys."""
    perms = mw._catalog_role_permissions("viewer")
    assert set(perms) == set(_viewer_perms())
    assert len(perms) == 3


def test_catalog_role_permissions_member():
    """_catalog_role_permissions('member') returns the 6 member keys."""
    perms = mw._catalog_role_permissions("member")
    assert set(perms) == set(_member_perms())


def test_catalog_role_permissions_unknown_returns_empty():
    """_catalog_role_permissions for an unknown role returns [] (fail-closed)."""
    assert mw._catalog_role_permissions("superuser") == []
    assert mw._catalog_role_permissions("") == []
    assert mw._catalog_role_permissions("ADMIN") == []  # case-sensitive
