"""Tests that the local-identity guard is wired to the correct routes (B2/B3/B4).

Strategy: introspect rbac.router.router.routes to assert that the
require_local_identity dependency is present on each affected route.
This is lightweight (no DB, no HTTP server needed) and fails precisely
when the decorator is missing.

Run with --noconftest:
  pytest .../tests/test_route_local_identity_guards.py --noconftest -q
"""
import pytest
from fastapi.routing import APIRoute

import services.api_gateway.rbac.router as rbac_router
from services.api_gateway.rbac.local_identity_guard import require_local_identity


def _guarded_routes():
    """Return a dict of {(method, path): [dependency_callables]} for all routes."""
    result = {}
    for route in rbac_router.router.routes:
        if not isinstance(route, APIRoute):
            continue
        deps = [d.dependency for d in (route.dependencies or [])]
        for method in route.methods or []:
            result[(method.upper(), route.path)] = deps
    return result


def _has_guard(method: str, path: str) -> bool:
    routes = _guarded_routes()
    deps = routes.get((method, path), [])
    return require_local_identity in deps


# ---------------------------------------------------------------------------
# B2 — POST /auth/switch-org must require local identity
# ---------------------------------------------------------------------------


def test_switch_org_requires_local_identity():
    """/switch-org must carry require_local_identity in its dependencies."""
    assert _has_guard("POST", "/auth/switch-org"), (
        "POST /auth/switch-org is missing require_local_identity guard"
    )


# ---------------------------------------------------------------------------
# B3 — TOTP endpoints must require local identity
# ---------------------------------------------------------------------------


def test_totp_setup_requires_local_identity():
    """/totp/setup must carry require_local_identity in its dependencies."""
    assert _has_guard("POST", "/auth/totp/setup"), (
        "POST /auth/totp/setup is missing require_local_identity guard"
    )


def test_totp_verify_requires_local_identity():
    """/totp/verify must carry require_local_identity in its dependencies."""
    assert _has_guard("POST", "/auth/totp/verify"), (
        "POST /auth/totp/verify is missing require_local_identity guard"
    )


def test_totp_disable_requires_local_identity():
    """/totp/disable must carry require_local_identity in its dependencies."""
    assert _has_guard("POST", "/auth/totp/disable"), (
        "POST /auth/totp/disable is missing require_local_identity guard"
    )


# ---------------------------------------------------------------------------
# B4 — POST /auth/accept-invite must require local identity
# ---------------------------------------------------------------------------


def test_accept_invite_requires_local_identity():
    """/accept-invite must carry require_local_identity in its dependencies."""
    assert _has_guard("POST", "/auth/accept-invite"), (
        "POST /auth/accept-invite is missing require_local_identity guard"
    )


# ---------------------------------------------------------------------------
# Sanity: public routes must NOT carry the guard
# ---------------------------------------------------------------------------


def test_login_does_not_require_local_identity():
    """/login is always public; it must NOT carry the local-identity guard."""
    assert not _has_guard("POST", "/auth/login"), (
        "POST /auth/login unexpectedly carries require_local_identity"
    )


def test_register_invite_has_guard():
    """/register-invite already carries the guard (was pre-existing)."""
    assert _has_guard("POST", "/auth/register-invite"), (
        "POST /auth/register-invite unexpectedly lost its require_local_identity guard"
    )


# ---------------------------------------------------------------------------
# Behaviour: guard blocks in external mode, passes in local mode
# ---------------------------------------------------------------------------


def test_switch_org_blocked_in_external_mode(monkeypatch):
    """In inferiaauth mode POST /switch-org returns 409."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import services.api_gateway.rbac.local_identity_guard as guard

    monkeypatch.setattr(guard.settings, "auth_provider", "inferiaauth", raising=False)

    app = FastAPI()
    app.include_router(rbac_router.router)
    client = TestClient(app, raise_server_exceptions=False)

    # We only need to verify the dependency fires; authentication is skipped
    # because TestClient does not enforce the middleware — the dependency
    # itself returns 409 before the handler is reached.
    resp = client.post("/auth/switch-org", json={"org_id": "some-org"})
    assert resp.status_code == 409


def test_totp_setup_blocked_in_external_mode(monkeypatch):
    """In oidc mode POST /totp/setup returns 409."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import services.api_gateway.rbac.local_identity_guard as guard

    monkeypatch.setattr(guard.settings, "auth_provider", "oidc", raising=False)

    app = FastAPI()
    app.include_router(rbac_router.router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/auth/totp/setup")
    assert resp.status_code == 409


def test_totp_verify_blocked_in_external_mode(monkeypatch):
    """In inferiaauth mode POST /totp/verify returns 409."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import services.api_gateway.rbac.local_identity_guard as guard

    monkeypatch.setattr(guard.settings, "auth_provider", "inferiaauth", raising=False)

    app = FastAPI()
    app.include_router(rbac_router.router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/auth/totp/verify", json={"totp_code": "123456"})
    assert resp.status_code == 409


def test_totp_disable_blocked_in_external_mode(monkeypatch):
    """In inferiaauth mode POST /totp/disable returns 409."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import services.api_gateway.rbac.local_identity_guard as guard

    monkeypatch.setattr(guard.settings, "auth_provider", "inferiaauth", raising=False)

    app = FastAPI()
    app.include_router(rbac_router.router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/auth/totp/disable")
    assert resp.status_code == 409


def test_accept_invite_blocked_in_external_mode(monkeypatch):
    """In inferiaauth mode POST /accept-invite returns 409."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import services.api_gateway.rbac.local_identity_guard as guard

    monkeypatch.setattr(guard.settings, "auth_provider", "inferiaauth", raising=False)

    app = FastAPI()
    app.include_router(rbac_router.router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/auth/accept-invite", params={"token": "some-token"})
    assert resp.status_code == 409
