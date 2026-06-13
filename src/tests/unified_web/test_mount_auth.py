"""Regression tests for root_path-aware middleware under the /api sub-app mount.

ROOT CAUSE these guard against: when the gateway is mounted via
``parent.mount("/api", gateway_app)``, Starlette sets
``scope['root_path']='/api'`` but does NOT strip ``/api`` from
``request.url.path``. The gateway's auth middleware reads ``request.url.path``
for all its skip/public-path decisions, so under the mount EVERY path-prefix and
public-path check compared against the WRONG (un-stripped) path — workers could
not register (401), engine model pulls failed (401), and login/health/docs were
all 401. The fix strips ``root_path`` before any decision check; it must be a
NO-OP in standalone (un-mounted, ``root_path == ''``) mode.

Layers covered:

1. Unit — ``_route_path`` strips ``root_path`` under the mount, no-op when ''.
2. Unit — ``proxy_admin_aws_discovery`` derives the orchestration-relative
   upstream path (``v1/admin/aws/...``, NOT ``api/v1/admin/aws/...``) under the
   mount.
3. Integration — a REAL ``parent.mount("/api", gateway_app)`` proves the auth
   middleware does NOT 401 the skip/public paths under the mount.
"""

import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# 1) Unit — _route_path
# ---------------------------------------------------------------------------


def _make_request(url_path: str, root_path: str):
    """A duck-typed stand-in for starlette Request exposing url.path + scope."""
    return SimpleNamespace(
        url=SimpleNamespace(path=url_path),
        scope={"root_path": root_path},
    )


def test_route_path_strips_root_under_mount():
    """Under the /api mount, root_path='/api' is stripped to the gateway path."""
    from api_gateway.rbac.middleware import _route_path

    req = _make_request("/api/v1/workers/register", "/api")
    assert _route_path(req) == "/v1/workers/register"


def test_route_path_noop_when_root_empty():
    """Standalone (un-mounted) mode: root_path='' is a no-op."""
    from api_gateway.rbac.middleware import _route_path

    req = _make_request("/v1/workers/register", "")
    assert _route_path(req) == "/v1/workers/register"


def test_route_path_exact_root_yields_slash():
    """root_path == full path leaves a leading-slash route path, not ''."""
    from api_gateway.rbac.middleware import _route_path

    req = _make_request("/api", "/api")
    assert _route_path(req) == "/"


def test_route_path_no_match_returns_unchanged():
    """If the path does not start with root_path, return it unchanged."""
    from api_gateway.rbac.middleware import _route_path

    req = _make_request("/v1/foo", "/api")
    assert _route_path(req) == "/v1/foo"


def test_route_path_missing_scope_key():
    """Absent root_path scope key defaults to no-op."""
    from api_gateway.rbac.middleware import _route_path

    req = SimpleNamespace(url=SimpleNamespace(path="/auth/login"), scope={})
    assert _route_path(req) == "/auth/login"


def test_route_path_non_string_root_is_noop():
    """A non-str root_path (e.g. a test MagicMock scope) must NOT raise — no-op.

    Existing middleware unit tests build ``request = MagicMock()`` and set only
    ``request.url.path`` to a real string, leaving ``request.scope`` a MagicMock
    whose ``.get`` returns a truthy MagicMock. The helper must treat a non-str
    root_path as 'no mount' and return the path unchanged rather than raising
    TypeError in ``path.startswith(root)``.
    """
    from unittest.mock import MagicMock

    from api_gateway.rbac.middleware import _route_path

    req = MagicMock()
    req.url.path = "/some/protected"
    assert _route_path(req) == "/some/protected"


def test_common_route_path_matches_rbac():
    """common.middleware._route_path mirrors the rbac helper semantics."""
    from common.middleware import _route_path as common_route_path

    assert (
        common_route_path(_make_request("/api/internal/log", "/api"))
        == "/internal/log"
    )
    assert (
        common_route_path(_make_request("/internal/log", ""))
        == "/internal/log"
    )


# ---------------------------------------------------------------------------
# 2) Unit — proxy_admin_aws_discovery derives orchestration-relative upstream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proxy_admin_aws_discovery_strips_root_path():
    """Under the /api mount the upstream path is v1/admin/aws/..., not api/...."""
    from api_gateway.gateway import proxy_routes

    captured = {}

    async def _fake_proxy_request(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200)

    # Request mounted at /api: url.path is un-stripped, root_path is /api.
    request = SimpleNamespace(
        method="GET",
        url=SimpleNamespace(path="/api/v1/admin/aws/regions"),
        scope={"root_path": "/api"},
    )
    user_context = object()

    with patch.object(proxy_routes, "proxy_request", _fake_proxy_request), patch.object(
        proxy_routes.authz_service, "require_permission", lambda *a, **k: None
    ):
        await proxy_routes.proxy_admin_aws_discovery(
            request=request, user_context=user_context
        )

    assert captured["path"] == "v1/admin/aws/regions"
    assert not captured["path"].startswith("api/")


@pytest.mark.asyncio
async def test_proxy_admin_aws_discovery_standalone_noop():
    """Standalone (root_path='') still yields v1/admin/aws/... (no-op strip)."""
    from api_gateway.gateway import proxy_routes

    captured = {}

    async def _fake_proxy_request(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200)

    request = SimpleNamespace(
        method="GET",
        url=SimpleNamespace(path="/v1/admin/aws/instance-types"),
        scope={"root_path": ""},
    )

    with patch.object(proxy_routes, "proxy_request", _fake_proxy_request), patch.object(
        proxy_routes.authz_service, "require_permission", lambda *a, **k: None
    ):
        await proxy_routes.proxy_admin_aws_discovery(
            request=request, user_context=object()
        )

    assert captured["path"] == "v1/admin/aws/instance-types"


# ---------------------------------------------------------------------------
# 3) Integration — REAL parent.mount("/api", gateway_app)
# ---------------------------------------------------------------------------
#
# Build the real gateway app mounted at /api and drive its lifespan with the DB
# seed / config polling / catalog declare side effects neutralised (same pattern
# as test_unified_app.py). Then prove the auth middleware does NOT 401 the skip /
# public paths under the mount — the production bug was every one of these 401'd.


@pytest_asyncio.fixture
async def mounted_client(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport

    # --- Gateway startup/shutdown side effects -> no-ops ---
    monkeypatch.setattr(
        "api_gateway.rbac.initialization.initialize_default_org", AsyncMock()
    )
    monkeypatch.setattr("api_gateway.app._maybe_declare_catalog", AsyncMock())

    from api_gateway.management.config_manager import config_manager

    monkeypatch.setattr(config_manager, "initialize", AsyncMock())
    monkeypatch.setattr(config_manager, "start_polling", lambda *a, **k: None)
    monkeypatch.setattr(config_manager, "stop_polling", lambda *a, **k: None)

    from api_gateway.gateway.http_client import gateway_http_client
    from api_gateway.gateway.rate_limiter import rate_limiter

    monkeypatch.setattr(gateway_http_client, "close_all", AsyncMock())
    monkeypatch.setattr(rate_limiter, "close", AsyncMock())

    @asynccontextmanager
    async def _fake_session():
        yield AsyncMock()

    monkeypatch.setattr(
        "api_gateway.app.AsyncSessionLocal",
        lambda *a, **k: _fake_session(),
        raising=False,
    )

    # Import the REAL gateway app and mount it at /api on a bare parent.
    gw_app_mod = importlib.import_module("api_gateway.app")
    gateway_app = gw_app_mod.app

    parent = FastAPI()
    parent.mount("/api", gateway_app)

    # Drive the gateway lifespan (real startup, mocked effects) via the parent.
    async with gateway_app.router.lifespan_context(gateway_app):
        async with AsyncClient(
            transport=ASGITransport(app=parent, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            yield client


@pytest.mark.asyncio
async def test_worker_register_not_401_under_mount(mounted_client):
    """POST /api/v1/workers/register must NOT be 401/403 (auth skip fires).

    It will fail downstream (proxy to absent orchestration), but the auth
    middleware must let it through — the production bug 401'd every worker.
    """
    resp = await mounted_client.post("/api/v1/workers/register")
    assert resp.status_code not in (401, 403), resp.text


@pytest.mark.asyncio
async def test_hf_passthrough_not_401_under_mount(mounted_client):
    """GET /api/hf/* (engine model pulls) must NOT be 401/403 under the mount."""
    resp = await mounted_client.get("/api/hf/whatever")
    assert resp.status_code not in (401, 403), resp.text


@pytest.mark.asyncio
async def test_login_public_path_not_401_under_mount(mounted_client):
    """POST /api/auth/login reaches the handler (422/400), NOT a middleware 401.

    The public-path skip must fire for the mount-relative '/auth/login'; an
    empty body then fails request-body validation (422), proving routing was
    reached rather than the middleware rejecting with 401.
    """
    resp = await mounted_client.post("/api/auth/login")
    assert resp.status_code != 401, resp.text
    # Reached the login handler / its body validation rather than auth reject.
    assert resp.status_code in (400, 422), resp.text


@pytest.mark.asyncio
async def test_health_public_path_not_401_under_mount(mounted_client):
    """GET /api/health must reach the health route, NOT a middleware 401."""
    resp = await mounted_client.get("/api/health")
    assert resp.status_code != 401, resp.text


@pytest.mark.asyncio
async def test_protected_path_still_401_under_mount(mounted_client):
    """A NON-skip protected path is STILL 401 unauthenticated under the mount.

    Proves the fix did not broaden the auth skip — only the listed skip/public
    paths are exempt; everything else still requires a token.
    """
    resp = await mounted_client.get("/api/v1/deployments")
    assert resp.status_code == 401, resp.text
