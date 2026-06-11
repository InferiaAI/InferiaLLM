"""Tests for TOTP setup security — secret must not be written to totp_secret before verification."""

import pytest
import pytest_asyncio
import pyotp
from unittest.mock import AsyncMock, MagicMock, patch

from api_gateway.rbac.router import totp_setup, totp_verify
from api_gateway.db.models import User as DBUser
from api_gateway.models import UserContext, TOTPVerifyRequest


def _make_user(**overrides):
    """Create a mock user with sensible defaults."""
    user = MagicMock(spec=DBUser)
    user.id = overrides.get("id", "user-totp-001")
    user.email = overrides.get("email", "totp@com")
    user.password_hash = "hashed"
    user.totp_secret = overrides.get("totp_secret", None)
    user.totp_pending_secret = overrides.get("totp_pending_secret", None)
    user.totp_enabled = overrides.get("totp_enabled", False)
    user.default_org_id = "org-001"
    return user


def _make_request(user_context):
    """Create a mock Request with user context on state."""
    request = MagicMock()
    request.state.user = user_context
    return request


def _make_db(user):
    """Create a mock async DB session that returns *user* on any execute."""
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = user
    db.execute.return_value = result
    return db


def _user_context():
    return UserContext(
        user_id="user-totp-001",
        username="totp@com",
        email="totp@com",
        roles=["admin"],
        permissions=[],
        org_id="org-001",
        quota_limit=10000,
        quota_used=0,
    )


# ---------------------------------------------------------------------------
# totp_setup — must write to totp_pending_secret, NOT totp_secret
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_totp_setup_writes_to_pending_secret():
    """After calling totp_setup, the new secret must land in totp_pending_secret,
    and the existing totp_secret must remain unchanged."""
    user = _make_user(totp_secret=None)
    db = _make_db(user)
    request = _make_request(_user_context())

    with patch(
        "api_gateway.rbac.router.get_current_user_from_request",
        return_value=_user_context(),
    ):
        response = await totp_setup(request=request, db=db)

    # The pending column must have been set to a non-empty base32 string
    assert user.totp_pending_secret is not None
    assert len(user.totp_pending_secret) > 0

    # The real totp_secret must NOT have been touched
    assert user.totp_secret is None

    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_totp_setup_does_not_overwrite_active_secret():
    """If the user already has an active totp_secret (2FA enabled), calling
    setup must NOT overwrite it — the new secret goes to pending only."""
    existing_secret = pyotp.random_base32()
    user = _make_user(totp_secret=existing_secret, totp_enabled=True)
    db = _make_db(user)
    request = _make_request(_user_context())

    with patch(
        "api_gateway.rbac.router.get_current_user_from_request",
        return_value=_user_context(),
    ):
        await totp_setup(request=request, db=db)

    # Active secret unchanged
    assert user.totp_secret == existing_secret
    # New secret stored in pending
    assert user.totp_pending_secret is not None
    assert user.totp_pending_secret != existing_secret


# ---------------------------------------------------------------------------
# totp_verify — must read pending, promote to totp_secret on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_totp_verify_promotes_pending_to_active():
    """After successful verification the pending secret is promoted to
    totp_secret, pending is cleared, and totp_enabled is True."""
    secret = pyotp.random_base32()
    valid_code = pyotp.TOTP(secret).now()

    user = _make_user(totp_pending_secret=secret, totp_secret=None)
    db = _make_db(user)
    request = _make_request(_user_context())
    payload = TOTPVerifyRequest(totp_code=valid_code)

    with patch(
        "api_gateway.rbac.router.get_current_user_from_request",
        return_value=_user_context(),
    ):
        response = await totp_verify(payload=payload, request=request, db=db)

    assert user.totp_secret == secret
    assert user.totp_pending_secret is None
    assert user.totp_enabled is True
    assert db.commit.await_count >= 1


@pytest.mark.asyncio
async def test_totp_verify_rejects_invalid_code():
    """An incorrect TOTP code must be rejected and nothing promoted."""
    from fastapi import HTTPException

    secret = pyotp.random_base32()
    user = _make_user(totp_pending_secret=secret, totp_secret=None)
    db = _make_db(user)
    request = _make_request(_user_context())
    payload = TOTPVerifyRequest(totp_code="000000")

    with patch(
        "api_gateway.rbac.router.get_current_user_from_request",
        return_value=_user_context(),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await totp_verify(payload=payload, request=request, db=db)

    assert exc_info.value.status_code == 400
    # Secret must NOT have been promoted
    assert user.totp_secret is None
    assert user.totp_pending_secret == secret


@pytest.mark.asyncio
async def test_totp_verify_fails_without_pending_secret():
    """If there is no pending secret, verify must fail with 400."""
    from fastapi import HTTPException

    user = _make_user(totp_pending_secret=None, totp_secret=None)
    db = _make_db(user)
    request = _make_request(_user_context())
    payload = TOTPVerifyRequest(totp_code="123456")

    with patch(
        "api_gateway.rbac.router.get_current_user_from_request",
        return_value=_user_context(),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await totp_verify(payload=payload, request=request, db=db)

    assert exc_info.value.status_code == 400
