"""Tests for catalog declaration wired into app startup lifespan.

app.py has heavy transitive imports (websockets, jwt 1.3.1 conflict, DB drivers).
We stub out the modules that cause ImportError before importing appmod, so the
tests run without a full environment.  Only _maybe_declare_catalog and settings
are exercised here.
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Path fix: pyenv PyJWT must shadow the conflicting jwt 1.3.1 at ~/.local/lib
# ---------------------------------------------------------------------------
_PYENV_SITE = "/home/celestix/.pyenv/versions/3.12.9/lib/python3.12/site-packages"
_LOCAL_SITE = "/home/celestix/.local/lib/python3.12/site-packages"
sys.path = [_PYENV_SITE] + [p for p in sys.path if p != _LOCAL_SITE and p != _PYENV_SITE]

# ---------------------------------------------------------------------------
# Stub modules that are not installed in the test environment
# ---------------------------------------------------------------------------
def _stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    return mod


def _ensure_stub(name: str, **attrs) -> None:
    """Insert a stub only if the real module isn't importable."""
    if name not in sys.modules:
        sys.modules[name] = _stub(name, **attrs)


# websockets (not installed in test env)
_ensure_stub("websockets")
_ensure_stub("websockets.exceptions")

# asyncpg / SQLAlchemy drivers that may not be present
_ensure_stub("asyncpg")

# ---------------------------------------------------------------------------
# Now import appmod — all heavy transitive deps are either real or stubbed
# ---------------------------------------------------------------------------
import pytest  # noqa: E402
import inferia.services.api_gateway.app as appmod  # noqa: E402


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_declares_in_external_mode(monkeypatch):
    monkeypatch.setattr(appmod.settings, "auth_provider", "external")
    monkeypatch.setattr(appmod.settings, "external_auth_url", "https://auth.example.com")
    monkeypatch.setattr(appmod.settings, "catalog_admin_token", "tok")
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("inferia.services.api_gateway.rbac.catalog_declare.declare_catalog", mock)
    await appmod._maybe_declare_catalog()
    mock.assert_awaited_once_with("https://auth.example.com", "tok")


@pytest.mark.asyncio
async def test_no_declare_in_local_mode(monkeypatch):
    monkeypatch.setattr(appmod.settings, "auth_provider", "local")
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("inferia.services.api_gateway.rbac.catalog_declare.declare_catalog", mock)
    await appmod._maybe_declare_catalog()
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_declare_without_token(monkeypatch):
    monkeypatch.setattr(appmod.settings, "auth_provider", "external")
    monkeypatch.setattr(appmod.settings, "external_auth_url", "https://auth.example.com")
    monkeypatch.setattr(appmod.settings, "catalog_admin_token", None)
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("inferia.services.api_gateway.rbac.catalog_declare.declare_catalog", mock)
    await appmod._maybe_declare_catalog()
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_declare_without_external_auth_url(monkeypatch):
    monkeypatch.setattr(appmod.settings, "auth_provider", "external")
    monkeypatch.setattr(appmod.settings, "external_auth_url", None)
    monkeypatch.setattr(appmod.settings, "catalog_admin_token", "tok")
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("inferia.services.api_gateway.rbac.catalog_declare.declare_catalog", mock)
    await appmod._maybe_declare_catalog()
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_declare_failure_is_non_fatal(monkeypatch):
    """A False return from declare_catalog must not raise."""
    monkeypatch.setattr(appmod.settings, "auth_provider", "external")
    monkeypatch.setattr(appmod.settings, "external_auth_url", "https://auth.example.com")
    monkeypatch.setattr(appmod.settings, "catalog_admin_token", "tok")
    mock = AsyncMock(return_value=False)
    monkeypatch.setattr("inferia.services.api_gateway.rbac.catalog_declare.declare_catalog", mock)
    # Must complete without raising
    await appmod._maybe_declare_catalog()
    mock.assert_awaited_once_with("https://auth.example.com", "tok")
