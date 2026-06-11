"""Tests for WebSocket token resolution (B1).

Verifies that resolve_token_to_user_context (the shared helper) branches
correctly across the three auth modes, and that _get_ws_user_context
delegates to it (proven indirectly via middleware unit-tests — proxy_routes
cannot be imported locally because the `websockets` package is not installed
in the test environment).

Run with --noconftest:
  pytest .../tests/test_ws_auth_modes.py --noconftest -q
"""
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi import HTTPException

import services.api_gateway.rbac.middleware as mw
from services.api_gateway.models import UserContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(**kwargs) -> UserContext:
    defaults = dict(
        user_id="u1",
        username="a@b.com",
        email="a@b.com",
        roles=["admin"],
        permissions=[],
        org_id="org1",
        quota_limit=10000,
        quota_used=0,
    )
    defaults.update(kwargs)
    return UserContext(**defaults)


def _http_exc(code=401):
    return HTTPException(status_code=code, detail="bad token")


# ---------------------------------------------------------------------------
# resolve_token_to_user_context — unit-test the 3-way branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_mode_calls_local_resolver(monkeypatch):
    """local mode: only _resolve_local_token is called."""
    monkeypatch.setattr(mw.settings, "auth_provider", "local", raising=False)

    local_ok = AsyncMock(return_value=_ctx())
    ext_mock = AsyncMock()
    oidc_mock = AsyncMock()

    monkeypatch.setattr(mw, "_resolve_local_token", local_ok)
    monkeypatch.setattr(mw, "_resolve_external_token", ext_mock)
    monkeypatch.setattr(mw, "_resolve_oidc_token", oidc_mock)

    result = await mw.resolve_token_to_user_context(MagicMock(), "tok")

    local_ok.assert_awaited_once()
    ext_mock.assert_not_awaited()
    oidc_mock.assert_not_awaited()
    assert result.user_id == "u1"


@pytest.mark.asyncio
async def test_local_mode_propagates_failure(monkeypatch):
    """local mode: HTTPException from local resolver propagates to caller."""
    monkeypatch.setattr(mw.settings, "auth_provider", "local", raising=False)
    monkeypatch.setattr(
        mw, "_resolve_local_token", AsyncMock(side_effect=_http_exc())
    )

    with pytest.raises(HTTPException) as exc_info:
        await mw.resolve_token_to_user_context(MagicMock(), "bad-tok")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_inferiaauth_mode_tries_local_first_success(monkeypatch):
    """inferiaauth mode: if local succeeds (superadmin), external is NOT called."""
    monkeypatch.setattr(mw.settings, "auth_provider", "inferiaauth", raising=False)

    local_ok = AsyncMock(return_value=_ctx(user_id="superadmin"))
    ext_mock = AsyncMock()

    monkeypatch.setattr(mw, "_resolve_local_token", local_ok)
    monkeypatch.setattr(mw, "_resolve_external_token", ext_mock)

    result = await mw.resolve_token_to_user_context(MagicMock(), "tok")

    local_ok.assert_awaited_once()
    ext_mock.assert_not_awaited()
    assert result.user_id == "superadmin"


@pytest.mark.asyncio
async def test_inferiaauth_mode_falls_back_to_external(monkeypatch):
    """inferiaauth mode: local failure triggers _resolve_external_token."""
    monkeypatch.setattr(mw.settings, "auth_provider", "inferiaauth", raising=False)

    local_fail = AsyncMock(side_effect=_http_exc())
    ext_ok = AsyncMock(return_value=_ctx(user_id="ext-u1"))

    monkeypatch.setattr(mw, "_resolve_local_token", local_fail)
    monkeypatch.setattr(mw, "_resolve_external_token", ext_ok)

    result = await mw.resolve_token_to_user_context(MagicMock(), "idp-tok")

    local_fail.assert_awaited_once()
    ext_ok.assert_awaited_once()
    assert result.user_id == "ext-u1"


@pytest.mark.asyncio
async def test_inferiaauth_mode_both_fail_raises(monkeypatch):
    """inferiaauth mode: both resolvers failing propagates the external exception."""
    monkeypatch.setattr(mw.settings, "auth_provider", "inferiaauth", raising=False)

    monkeypatch.setattr(
        mw, "_resolve_local_token", AsyncMock(side_effect=_http_exc())
    )
    monkeypatch.setattr(
        mw, "_resolve_external_token", AsyncMock(side_effect=_http_exc(401))
    )

    with pytest.raises(HTTPException) as exc_info:
        await mw.resolve_token_to_user_context(MagicMock(), "invalid")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_oidc_mode_tries_local_first_success(monkeypatch):
    """oidc mode: if local succeeds (superadmin), oidc resolver is NOT called."""
    monkeypatch.setattr(mw.settings, "auth_provider", "oidc", raising=False)

    local_ok = AsyncMock(return_value=_ctx(user_id="superadmin"))
    oidc_mock = AsyncMock()

    monkeypatch.setattr(mw, "_resolve_local_token", local_ok)
    monkeypatch.setattr(mw, "_resolve_oidc_token", oidc_mock)

    result = await mw.resolve_token_to_user_context(MagicMock(), "tok")

    local_ok.assert_awaited_once()
    oidc_mock.assert_not_awaited()
    assert result.user_id == "superadmin"


@pytest.mark.asyncio
async def test_oidc_mode_falls_back_to_oidc_resolver(monkeypatch):
    """oidc mode: local failure triggers _resolve_oidc_token."""
    monkeypatch.setattr(mw.settings, "auth_provider", "oidc", raising=False)

    local_fail = AsyncMock(side_effect=_http_exc())
    oidc_ok = AsyncMock(return_value=_ctx(user_id="oidc-u1", roles=["viewer"]))

    monkeypatch.setattr(mw, "_resolve_local_token", local_fail)
    monkeypatch.setattr(mw, "_resolve_oidc_token", oidc_ok)

    result = await mw.resolve_token_to_user_context(MagicMock(), "oidc-tok")

    local_fail.assert_awaited_once()
    oidc_ok.assert_awaited_once()
    assert result.user_id == "oidc-u1"


@pytest.mark.asyncio
async def test_oidc_mode_both_fail_raises(monkeypatch):
    """oidc mode: both resolvers failing propagates the oidc exception."""
    monkeypatch.setattr(mw.settings, "auth_provider", "oidc", raising=False)

    monkeypatch.setattr(
        mw, "_resolve_local_token", AsyncMock(side_effect=_http_exc())
    )
    monkeypatch.setattr(
        mw, "_resolve_oidc_token", AsyncMock(side_effect=_http_exc(401))
    )

    with pytest.raises(HTTPException) as exc_info:
        await mw.resolve_token_to_user_context(MagicMock(), "invalid")

    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Structural: _get_ws_user_context delegates to resolve_token_to_user_context
# ---------------------------------------------------------------------------
# We cannot import proxy_routes directly (websockets package absent locally).
# Instead we verify that resolve_token_to_user_context is exported from
# middleware and that proxy_routes imports it — proven at py_compile time and
# via the module-level import check below.


def test_resolve_token_exported_from_middleware():
    """resolve_token_to_user_context must be importable from middleware."""
    from services.api_gateway.rbac.middleware import resolve_token_to_user_context  # noqa: F401
    import inspect
    assert inspect.iscoroutinefunction(resolve_token_to_user_context)


def test_proxy_routes_imports_resolve_token(monkeypatch):
    """proxy_routes module must import resolve_token_to_user_context.

    We verify this by checking the source without executing the module
    (to avoid the websockets import error at test collection time).
    """
    import ast
    import pathlib

    src = pathlib.Path(
        "/home/celestix/work/hooman/InferiaLLM/src/"
        "services/api_gateway/gateway/proxy_routes.py"
    ).read_text()

    tree = ast.parse(src)
    imported_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "middleware" in node.module:
                for alias in node.names:
                    imported_names.append(alias.name)

    assert "resolve_token_to_user_context" in imported_names, (
        "proxy_routes.py must import resolve_token_to_user_context from middleware"
    )


def test_get_ws_user_context_calls_resolve_token(monkeypatch):
    """_get_ws_user_context body must call resolve_token_to_user_context.

    Verified by AST-scanning the function body without executing the module.
    """
    import ast
    import pathlib

    src = pathlib.Path(
        "/home/celestix/work/hooman/InferiaLLM/src/"
        "services/api_gateway/gateway/proxy_routes.py"
    ).read_text()

    tree = ast.parse(src)
    called_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_get_ws_user_context":
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    func = child.func
                    if isinstance(func, ast.Name):
                        called_names.append(func.id)
                    elif isinstance(func, ast.Attribute):
                        called_names.append(func.attr)

    assert "resolve_token_to_user_context" in called_names, (
        "_get_ws_user_context must call resolve_token_to_user_context"
    )
