"""Tests for the unified parent web app (src/unified_web).

These cover three layers:

1. Routing STRUCTURE — hermetic, no lifespan, no DB. Inspect ``.routes`` to
   prove ``/v2`` is at the ROOT (not under ``/api``), ``/api`` and ``/inf`` are
   sub-app Mounts, and the SPA ``/`` catch-all is registered LAST.
2. Request DISPATCH + SPA — a real ``TestClient`` round-trip. The gateway
   lifespan does a real DB seed + config polling, so we monkeypatch those (and
   the inference shutdown httpx closers) to no-ops before building the app.
3. Lifespan PROPAGATION — the key correctness test. Starlette does NOT
   auto-run a mounted sub-app's lifespan; prove ``combined_lifespan`` drives
   BOTH children's startup AND shutdown.
"""

import importlib
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from starlette.routing import Mount

# NOTE: ``unified_web/__init__.py`` does ``from unified_web.app import app``,
# which rebinds the package attribute ``unified_web.app`` to the FastAPI
# instance and shadows the submodule. ``import unified_web.app as uw`` would
# therefore yield the app object, not the module. Resolve the MODULE explicitly
# via importlib so monkeypatch targets the module's globals.
uw = importlib.import_module("unified_web.app")


def _route_path(r) -> str:
    return getattr(r, "path", getattr(r, "path_format", ""))


def _is_root_mount(r) -> bool:
    """A mount at '/' — Starlette normalises the root mount's path to ''."""
    return isinstance(r, Mount) and _route_path(r) in ("", "/")


# ---------------------------------------------------------------------------
# 1) Routing structure — hermetic (no lifespan / DB).
# ---------------------------------------------------------------------------


def test_routing_structure_mounts_and_root_v2(monkeypatch, tmp_path):
    """build_unified_app registers /v2 at root, /api + /inf as Mounts, / last."""
    # A dashboard dir so the SPA "/" mount is present.
    (tmp_path / "index.html").write_text("<!doctype html><html></html>")
    monkeypatch.setenv("INFERIA_DASHBOARD_DIR", str(tmp_path))

    app = uw.build_unified_app()
    routes = app.routes

    mount_paths = {_route_path(r) for r in routes if isinstance(r, Mount)}
    assert "/api" in mount_paths
    assert "/inf" in mount_paths
    # SPA catch-all present when a dash dir exists (root mount path is "").
    assert any(_is_root_mount(r) for r in routes)

    # /v2/{path} lives at the parent ROOT (NOT a Mount, NOT under /api).
    v2_routes = [r for r in routes if "/v2/" in _route_path(r)]
    assert v2_routes, "expected a /v2/{path} route at the parent root"
    for r in v2_routes:
        assert not isinstance(r, Mount)
        # The literal root path, never /api/v2 or /inf/v2.
        assert not _route_path(r).startswith("/api")
        assert not _route_path(r).startswith("/inf")


def test_v2_registered_before_spa_catchall(monkeypatch, tmp_path):
    """The SPA '/' catch-all must be the LAST route or it shadows /v2,/api,/inf."""
    (tmp_path / "index.html").write_text("<!doctype html><html></html>")
    monkeypatch.setenv("INFERIA_DASHBOARD_DIR", str(tmp_path))

    app = uw.build_unified_app()
    paths_in_order = [_route_path(r) for r in app.routes]

    spa_index = next(
        i for i, r in enumerate(app.routes) if _is_root_mount(r)
    )
    api_index = next(
        i
        for i, r in enumerate(app.routes)
        if isinstance(r, Mount) and _route_path(r) == "/api"
    )
    inf_index = next(
        i
        for i, r in enumerate(app.routes)
        if isinstance(r, Mount) and _route_path(r) == "/inf"
    )
    v2_index = min(
        i for i, r in enumerate(app.routes) if "/v2/" in _route_path(r)
    )

    # Everything specific is registered before the "/" catch-all.
    assert v2_index < spa_index
    assert api_index < spa_index
    assert inf_index < spa_index
    # Sanity: the catch-all is genuinely last.
    assert spa_index == len(paths_in_order) - 1


def test_no_spa_mount_without_dashboard_dir(monkeypatch, tmp_path):
    """Importing/building is safe when the built dashboard dir is absent."""
    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("INFERIA_DASHBOARD_DIR", str(missing))

    app = uw.build_unified_app()
    mount_paths = {_route_path(r) for r in app.routes if isinstance(r, Mount)}
    # No SPA catch-all (root mount path would be "").
    assert not any(_is_root_mount(r) for r in app.routes)
    # Sub-apps + root /v2 are still wired.
    assert "/api" in mount_paths
    assert "/inf" in mount_paths
    assert any("/v2/" in _route_path(r) for r in app.routes)


# ---------------------------------------------------------------------------
# 2) Request dispatch + SPA — real ASGI round-trip with a neutralised lifespan.
# ---------------------------------------------------------------------------
#
# Starlette 0.35 + httpx 0.28 break ``fastapi.testclient.TestClient`` (it
# forwards ``app=`` to ``httpx.Client``, which httpx 0.28 removed). Use the
# repo's own pattern — ``httpx.AsyncClient`` over ``ASGITransport`` — and drive
# the unified app's OWN ``combined_lifespan`` via ``lifespan_context`` so the
# real startup/shutdown path (with its side effects neutralised) is exercised.


@pytest_asyncio.fixture
async def unified_client(monkeypatch, tmp_path):
    """An async ASGI client over the unified app with heavy startup mocked.

    The gateway lifespan does a real DB seed (initialize_default_org) + config
    polling, and the inference app closes httpx clients on shutdown. None of
    that infra exists in a unit test, so neutralise it before building the app.
    """
    from httpx import AsyncClient, ASGITransport

    # --- Gateway startup/shutdown side effects -> no-ops ---
    # initialize_default_org is imported INSIDE the gateway lifespan via
    # `from api_gateway.rbac.initialization import initialize_default_org`, so
    # patching the source attribute is what the lifespan actually resolves.
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

    # The gateway lifespan enters AsyncSessionLocal() as an async-cm; swap it
    # for a trivial one so no DB connection is attempted.
    @asynccontextmanager
    async def _fake_session():
        yield AsyncMock()

    monkeypatch.setattr(
        "api_gateway.app.AsyncSessionLocal",
        lambda *a, **k: _fake_session(),
        raising=False,
    )

    # --- Inference shutdown closers -> no-ops ---
    monkeypatch.setattr(
        "inference.core.http_client.http_client.close_client", AsyncMock()
    )
    monkeypatch.setattr(
        "inference.client.api_gateway_client.close_client", AsyncMock()
    )

    # --- A built SPA dir for the "/" mount ---
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><body>spa</body></html>"
    )
    (tmp_path / "config.js").write_text("window.__RUNTIME_CONFIG__ = {};")
    monkeypatch.setenv("INFERIA_DASHBOARD_DIR", str(tmp_path))

    app = uw.build_unified_app()
    # Drive the unified app's combined_lifespan (real startup, mocked effects).
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            yield client


@pytest.mark.asyncio
async def test_root_v2_route_exists_not_under_api(unified_client):
    """/v2/* resolves at the ROOT; /api/v2/* does NOT (v2 lifted off /api)."""
    # Reaches the root ollama mirror route; it tries to proxy to orchestration
    # (absent here) so it errors 5xx — but the route EXISTS, so it is NOT a 404
    # and NOT the SPA HTML shell.
    root_v2 = await unified_client.get("/v2/anything")
    assert root_v2.status_code != 404
    assert "<!doctype html" not in root_v2.text.lower()

    # Under /api there is no /v2 — the gateway no longer carries the mirror, so
    # the request is handled by the gateway (its auth middleware runs before
    # routing and rejects the unauthenticated request with a JSON error). The
    # invariant: it is NOT the root mirror (no 5xx proxy attempt) and NOT the
    # SPA — it is a gateway JSON response that never reached the v2 handler.
    api_v2 = await unified_client.get("/api/v2/anything")
    assert api_v2.status_code in (401, 403, 404)
    assert "<!doctype html" not in api_v2.text.lower()
    assert "detail" in api_v2.text  # gateway JSON error body


@pytest.mark.asyncio
async def test_spa_index_and_runtime_config(unified_client):
    """`/` serves index.html and `/config.js` serves the real asset."""
    root = await unified_client.get("/")
    assert "<!doctype html" in root.text.lower()

    cfg = await unified_client.get("/config.js")
    assert "window.__RUNTIME_CONFIG__" in cfg.text


@pytest.mark.asyncio
async def test_spa_deep_route_fallback(unified_client):
    """Unknown non-asset paths fall back to index.html (client-side routing)."""
    resp = await unified_client.get("/deep/spa/route")
    assert "<!doctype html" in resp.text.lower()


@pytest.mark.asyncio
async def test_unknown_api_path_is_json_error_not_spa(unified_client):
    """An unknown /api/* path is a JSON error from the gateway, NOT index.html.

    The gateway's auth middleware runs before routing, so an unauthenticated
    request to an unknown path is rejected with a JSON 401 (rather than a bare
    404). The load-bearing invariant for the unified mount is that /api/* is
    handled by the gateway sub-app and is NEVER served the SPA HTML shell.
    """
    resp = await unified_client.get("/api/totally-bogus")
    assert resp.status_code in (401, 403, 404)
    assert "<!doctype html" not in resp.text.lower()
    assert "detail" in resp.text  # gateway JSON error body, not the SPA


@pytest.mark.asyncio
async def test_subapps_reachable(unified_client):
    """/inf and /api dispatch into their sub-apps (route exists, != 404)."""
    assert (await unified_client.get("/inf/v1/models")).status_code != 404
    assert (await unified_client.post("/api/auth/login")).status_code != 404


# ---------------------------------------------------------------------------
# 3) Lifespan propagation — THE key correctness test (hermetic).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_combined_lifespan_drives_both_subapps(monkeypatch):
    """combined_lifespan must run startup AND shutdown of BOTH sub-apps.

    Starlette does NOT auto-run a mounted sub-app's lifespan, so the parent
    has to drive each child's lifespan_context explicitly. Swap the real
    gateway/inference apps for tiny flag-setting apps and assert all four
    flags flip.
    """
    from fastapi import FastAPI
    flags = {
        "gw_started": False,
        "gw_stopped": False,
        "inf_started": False,
        "inf_stopped": False,
    }

    @asynccontextmanager
    async def gw_lifespan(app):
        flags["gw_started"] = True
        yield
        flags["gw_stopped"] = True

    @asynccontextmanager
    async def inf_lifespan(app):
        flags["inf_started"] = True
        yield
        flags["inf_stopped"] = True

    fake_gateway = FastAPI(lifespan=gw_lifespan)
    fake_inference = FastAPI(lifespan=inf_lifespan)

    monkeypatch.setattr(uw, "gateway_app", fake_gateway)
    monkeypatch.setattr(uw, "inference_app", fake_inference)

    parent = FastAPI(lifespan=uw.combined_lifespan)

    async with parent.router.lifespan_context(parent):
        # Both children started before the parent yields control.
        assert flags["gw_started"] and flags["inf_started"]
        # Neither has shut down yet.
        assert not flags["gw_stopped"] and not flags["inf_stopped"]

    # Exiting the parent lifespan tore both children down.
    assert flags["gw_stopped"] and flags["inf_stopped"]
