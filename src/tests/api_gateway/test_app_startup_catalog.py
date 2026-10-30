"""Tests for catalog declaration wired into app startup lifespan."""

from unittest.mock import AsyncMock

import pytest

import api_gateway.app as appmod
from api_gateway.config import httpx_verify


def _assert_declared(mock):
    mock.assert_awaited_once_with(
        "https://auth.example.com",
        "tok",
        service_id=appmod.settings.external_service_id,
        verify=httpx_verify(appmod.settings),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_declares_in_inferiaauth_mode(monkeypatch):
    monkeypatch.setattr(appmod.settings, "auth_provider", "inferiaauth")
    monkeypatch.setattr(appmod.settings, "external_auth_url", "https://auth.example.com")
    monkeypatch.setattr(appmod.settings, "catalog_admin_token", "tok")
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("api_gateway.rbac.catalog_declare.declare_catalog", mock)
    await appmod._maybe_declare_catalog()
    _assert_declared(mock)


@pytest.mark.asyncio
async def test_no_declare_in_local_mode(monkeypatch):
    monkeypatch.setattr(appmod.settings, "auth_provider", "local")
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("api_gateway.rbac.catalog_declare.declare_catalog", mock)
    await appmod._maybe_declare_catalog()
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_declare_without_token(monkeypatch):
    monkeypatch.setattr(appmod.settings, "auth_provider", "inferiaauth")
    monkeypatch.setattr(appmod.settings, "external_auth_url", "https://auth.example.com")
    monkeypatch.setattr(appmod.settings, "catalog_admin_token", None)
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("api_gateway.rbac.catalog_declare.declare_catalog", mock)
    await appmod._maybe_declare_catalog()
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_declare_without_external_auth_url(monkeypatch):
    monkeypatch.setattr(appmod.settings, "auth_provider", "inferiaauth")
    monkeypatch.setattr(appmod.settings, "external_auth_url", None)
    monkeypatch.setattr(appmod.settings, "catalog_admin_token", "tok")
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("api_gateway.rbac.catalog_declare.declare_catalog", mock)
    await appmod._maybe_declare_catalog()
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_declare_in_oidc_mode(monkeypatch):
    """Catalog declaration only happens for inferiaauth, NOT generic oidc."""
    monkeypatch.setattr(appmod.settings, "auth_provider", "oidc")
    monkeypatch.setattr(appmod.settings, "external_auth_url", "https://auth.example.com")
    monkeypatch.setattr(appmod.settings, "catalog_admin_token", "tok")
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("api_gateway.rbac.catalog_declare.declare_catalog", mock)
    await appmod._maybe_declare_catalog()
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_declare_failure_is_non_fatal(monkeypatch):
    """A False return from declare_catalog must not raise."""
    monkeypatch.setattr(appmod.settings, "auth_provider", "inferiaauth")
    monkeypatch.setattr(appmod.settings, "external_auth_url", "https://auth.example.com")
    monkeypatch.setattr(appmod.settings, "catalog_admin_token", "tok")
    mock = AsyncMock(return_value=False)
    monkeypatch.setattr("api_gateway.rbac.catalog_declare.declare_catalog", mock)
    # Must complete without raising
    await appmod._maybe_declare_catalog()
    _assert_declared(mock)
