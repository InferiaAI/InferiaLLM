"""Tests for shadow-user provisioning (rbac/shadow_user.py) — B5.

Verifies that:
- On create, NO Organization select-limit-1 happens, default_org_id is None,
  and NO UserOrganization row is added.
- The empty-membership fallback no longer attaches to the first org.
- On lookup (user already exists), the existing row is reused unchanged.
- When memberships exist, the correct (org_id, role) pair is returned.

Style mirrors test_external_org.py: AsyncMock DB, no real DB connection.

Run with --noconftest:
  pytest .../tests/test_shadow_user.py --noconftest -q
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from api_gateway.rbac.shadow_user import get_or_create_shadow_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scalars_first(value):
    """Return a mock whose .scalars().first() == value."""
    m = MagicMock()
    m.scalars.return_value.first.return_value = value
    return m


def _scalars_all(values):
    """Return a mock whose .scalars().all() == values."""
    m = MagicMock()
    m.scalars.return_value.all.return_value = values
    return m


def _db(execute_results: list) -> AsyncMock:
    """Build a mock AsyncSession that returns execute_results in order."""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=execute_results)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _fake_user(email="a@b.com", default_org_id=None, memberships=None):
    user = MagicMock()
    user.id = "u-shadow-1"
    user.email = email
    user.default_org_id = default_org_id
    # memberships is resolved by a separate query; attach for convenience
    user._memberships = memberships or []
    return user


def _membership(org_id: str, role: str = "member", created_at=None):
    m = MagicMock()
    m.user_id = "u-shadow-1"
    m.org_id = org_id
    m.role = role
    m.created_at = created_at
    return m


# ---------------------------------------------------------------------------
# CREATE path — new user (no existing row)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_sets_default_org_id_none():
    """On create, the user must have default_org_id=None."""
    created_user = None

    def capture_add(obj):
        nonlocal created_user
        if hasattr(obj, "password_hash"):
            created_user = obj

    # execute sequence:
    # 1. select(User) by email → None  (user missing)
    # 2. select(UserOrganization) by user_id → no memberships
    db = _db([
        _scalars_first(None),      # user lookup → not found
        _scalars_all([]),          # membership lookup → empty
    ])
    db.add.side_effect = capture_add
    # refresh should populate the user; patch it to return the added object
    refreshed = _fake_user(default_org_id=None)
    db.refresh = AsyncMock(side_effect=lambda obj: None)

    user, org_id, roles = await get_or_create_shadow_user(
        db, email="new@ext.com", external_id="ext-001"
    )

    # The user object added to DB must have default_org_id=None
    assert created_user is not None
    assert created_user.default_org_id is None


@pytest.mark.asyncio
async def test_create_adds_no_user_organization_row():
    """On create, no UserOrganization row must be added."""
    uo_added = []

    def capture_add(obj):
        from api_gateway.db.models import UserOrganization
        if isinstance(obj, UserOrganization):
            uo_added.append(obj)

    db = _db([
        _scalars_first(None),  # user lookup → not found
        _scalars_all([]),      # membership lookup → empty
    ])
    db.add.side_effect = capture_add

    await get_or_create_shadow_user(db, email="new2@ext.com", external_id="ext-002")

    assert uo_added == [], (
        "Expected no UserOrganization rows to be created on shadow user creation"
    )


@pytest.mark.asyncio
async def test_create_does_not_query_organization_table():
    """On create, the code must NOT do a select(Organization).limit(1) query."""
    db = _db([
        _scalars_first(None),  # user lookup → not found
        _scalars_all([]),      # membership lookup
    ])

    await get_or_create_shadow_user(db, email="new3@ext.com", external_id="ext-003")

    # All executed queries should be only 2: user lookup + membership lookup.
    assert db.execute.await_count == 2, (
        f"Expected exactly 2 DB queries (user + membership), got {db.execute.await_count}. "
        "An Organization.limit(1) query must NOT occur on create."
    )


@pytest.mark.asyncio
async def test_create_returns_none_org_and_empty_roles_when_no_memberships():
    """On create with no memberships, return (user, None, [])."""
    db = _db([
        _scalars_first(None),  # user not found
        _scalars_all([]),      # no memberships
    ])

    user, org_id, roles = await get_or_create_shadow_user(
        db, email="new4@ext.com", external_id="ext-004"
    )

    assert org_id is None
    assert roles == []


# ---------------------------------------------------------------------------
# Empty-membership fallback path — must NOT attach to first org
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_fallback_attach_to_first_org_when_memberships_empty():
    """When an existing user has no memberships, the fallback MUST NOT
    attach them to the first Organization.limit(1) in the DB."""
    existing_user = _fake_user(default_org_id=None)

    db = _db([
        _scalars_first(existing_user),  # user found
        _scalars_all([]),               # memberships → empty
    ])

    user, org_id, roles = await get_or_create_shadow_user(
        db, email="existing@ext.com", external_id="ext-existing"
    )

    # Must return (user, None, []) — no attach to a random org
    assert org_id is None
    assert roles == []
    # Must NOT have added any rows
    db.add.assert_not_called()
    # Must NOT have committed (no rows to commit)
    db.commit.assert_not_awaited()
    # Only 2 queries: user lookup + membership lookup
    assert db.execute.await_count == 2, (
        "No extra Organization query should occur in the empty-membership fallback path"
    )


# ---------------------------------------------------------------------------
# EXISTING user with memberships — return correct (org_id, role)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_user_with_membership_returns_correct_org_and_role():
    """Existing user with a membership row returns that org and role."""
    mem = _membership(org_id="org-idp-1", role="admin")
    existing_user = _fake_user(default_org_id=None)

    db = _db([
        _scalars_first(existing_user),  # user found
        _scalars_all([mem]),            # memberships
    ])

    user, org_id, roles = await get_or_create_shadow_user(
        db, email="exists@ext.com", external_id="ext-existing-2"
    )

    assert org_id == "org-idp-1"
    assert roles == ["admin"]
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_existing_user_default_org_preferred_over_first_membership():
    """If default_org_id matches a membership, that membership's role wins."""
    mem1 = _membership(org_id="org-a", role="viewer")
    mem2 = _membership(org_id="org-b", role="admin")
    existing_user = _fake_user(default_org_id="org-b")

    db = _db([
        _scalars_first(existing_user),    # user found
        _scalars_all([mem1, mem2]),       # memberships
    ])

    user, org_id, roles = await get_or_create_shadow_user(
        db, email="pref@ext.com", external_id="ext-pref"
    )

    assert org_id == "org-b"
    assert roles == ["admin"]


@pytest.mark.asyncio
async def test_existing_user_falls_back_to_first_membership_when_default_not_found():
    """If default_org_id is set but doesn't match, fall back to first membership."""
    mem1 = _membership(org_id="org-x", role="member")
    existing_user = _fake_user(default_org_id="org-missing")

    db = _db([
        _scalars_first(existing_user),  # user found
        _scalars_all([mem1]),           # memberships
    ])

    user, org_id, roles = await get_or_create_shadow_user(
        db, email="fallback@ext.com", external_id="ext-fb"
    )

    assert org_id == "org-x"
    assert roles == ["member"]


# ---------------------------------------------------------------------------
# Email normalisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_is_lowercased_and_stripped():
    """Email is normalised to lowercase + stripped before lookup and creation."""
    captured = []

    def track_add(obj):
        if hasattr(obj, "email"):
            captured.append(obj.email)

    db = _db([
        _scalars_first(None),  # not found
        _scalars_all([]),      # no memberships
    ])
    db.add.side_effect = track_add

    await get_or_create_shadow_user(
        db, email="  Upper@Example.COM  ", external_id="ext-norm"
    )

    assert captured == ["upper@example.com"]
