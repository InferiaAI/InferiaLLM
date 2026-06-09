"""Tests for B6 — /auth/me must not expose shadow-row TOTP state in external modes.

The handler is tested by calling it directly with a monkeypatched DB session
and stubbed get_current_user_from_request.  settings.is_external_mode is
controlled via monkeypatching auth_provider (same approach as
test_local_identity_guard.py).

Run with --noconftest:
  pytest .../tests/test_me_totp_external_mode.py --noconftest -q
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inferia.services.api_gateway.rbac.router import get_current_user_info
from inferia.services.api_gateway.models import UserContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_context(**overrides):
    defaults = dict(
        user_id="u-me-1",
        username="me@inferia.com",
        email="me@inferia.com",
        roles=["admin"],
        permissions=[],
        org_id="org-1",
        quota_limit=10000,
        quota_used=0,
    )
    defaults.update(overrides)
    return UserContext(**defaults)


def _db_user(totp_enabled: bool = True):
    """Simulate a DB User row whose totp_enabled may be True."""
    user = MagicMock()
    user.id = "u-me-1"
    user.email = "me@inferia.com"
    user.totp_enabled = totp_enabled
    user.created_at = datetime(2024, 1, 1)
    return user


def _make_db(db_user):
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = db_user
    db.execute.return_value = result
    return db


async def _call_me(monkeypatch, auth_provider: str, totp_enabled: bool) -> dict:
    """Call get_current_user_info and return the Pydantic model as a dict."""
    import inferia.services.api_gateway.rbac.router as rtr
    import inferia.services.api_gateway.rbac.local_identity_guard as guard

    monkeypatch.setattr(rtr.settings, "auth_provider", auth_provider, raising=False)
    monkeypatch.setattr(guard.settings, "auth_provider", auth_provider, raising=False)

    ctx = _user_context()
    db = _make_db(_db_user(totp_enabled=totp_enabled))
    request = MagicMock()
    request.state.user = ctx

    with patch(
        "inferia.services.api_gateway.rbac.router.get_current_user_from_request",
        return_value=ctx,
    ):
        response = await get_current_user_info(request=request, db=db)

    return response.model_dump()


# ---------------------------------------------------------------------------
# External modes — totp_enabled must always be False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_inferiaauth_suppresses_totp_enabled(monkeypatch):
    """In inferiaauth mode, totp_enabled=True in DB must surface as False in /me."""
    data = await _call_me(monkeypatch, "inferiaauth", totp_enabled=True)
    assert data["totp_enabled"] is False, (
        "totp_enabled must be False in inferiaauth mode regardless of DB value"
    )


@pytest.mark.asyncio
async def test_me_oidc_suppresses_totp_enabled(monkeypatch):
    """In oidc mode, totp_enabled=True in DB must surface as False in /me."""
    data = await _call_me(monkeypatch, "oidc", totp_enabled=True)
    assert data["totp_enabled"] is False, (
        "totp_enabled must be False in oidc mode regardless of DB value"
    )


@pytest.mark.asyncio
async def test_me_external_mode_totp_false_stays_false(monkeypatch):
    """In external mode, totp_enabled=False in DB also surfaces as False."""
    data = await _call_me(monkeypatch, "inferiaauth", totp_enabled=False)
    assert data["totp_enabled"] is False


# ---------------------------------------------------------------------------
# Local mode — totp_enabled reflects the actual DB value
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_local_mode_reports_totp_enabled_true(monkeypatch):
    """In local mode, totp_enabled=True in DB surfaces as True in /me."""
    data = await _call_me(monkeypatch, "local", totp_enabled=True)
    assert data["totp_enabled"] is True, (
        "totp_enabled must reflect the DB value in local mode"
    )


@pytest.mark.asyncio
async def test_me_local_mode_reports_totp_enabled_false(monkeypatch):
    """In local mode, totp_enabled=False in DB surfaces as False in /me."""
    data = await _call_me(monkeypatch, "local", totp_enabled=False)
    assert data["totp_enabled"] is False


# ---------------------------------------------------------------------------
# Other fields are unaffected by the auth mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_email_and_org_unaffected_by_mode(monkeypatch):
    """Fields other than totp_enabled are returned correctly regardless of mode."""
    for provider in ("local", "inferiaauth", "oidc"):
        data = await _call_me(monkeypatch, provider, totp_enabled=False)
        assert data["email"] == "me@inferia.com"
        assert data["org_id"] == "org-1"
        assert data["roles"] == ["admin"]
