"""Unified parent ASGI app — single web port for the whole surface.

Collapses the three historical web ports into one in-process app:

* ``/api``  → the api_gateway app (control plane: auth, RBAC, proxy)
* ``/inf``  → the inference app  (data plane: OpenAI-compatible endpoints)
* ``/v2/*`` → the OCI registry mirror, at the ROOT (the OCI spec hard-codes
              ``<host>/v2``, so it CANNOT live under ``/api``)
* ``/``     → the built dashboard SPA (StaticFiles + index.html fallback)

The route/mount registration ORDER is load-bearing: ``/v2``, ``/api`` and
``/inf`` are all registered BEFORE the ``/`` catch-all, or the SPA mount would
shadow them.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api_gateway.app import app as gateway_app
from api_gateway.gateway.proxy_routes import ollama_registry_router
from api_gateway.rbac.oauth_router import router as oauth_router
from inference.app import app as inference_app
from unified_web.spa import SPAStaticFiles


def _dashboard_dir() -> str:
    """Resolve the built dashboard directory (``<repo>/src/dashboard``).

    Mirrors ``run_dashboard`` but WITHOUT ``os.chdir`` — the unified app must
    not change the process working directory. Overridable via
    ``INFERIA_DASHBOARD_DIR`` (used by tests + custom deployments).
    """
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # -> src/
    return os.environ.get("INFERIA_DASHBOARD_DIR", os.path.join(base, "dashboard"))


@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    """Drive BOTH mounted sub-apps' lifespans under the unified app.

    Starlette does NOT auto-run a mounted sub-app's lifespan, so without this
    the gateway's startup (DB seed, config polling, catalog declare) and the
    inference app's shutdown (httpx client close) would never fire. Nest the
    two child lifespan contexts so startup runs gateway-then-inference and
    shutdown unwinds inference-then-gateway.
    """
    async with gateway_app.router.lifespan_context(gateway_app):
        async with inference_app.router.lifespan_context(inference_app):
            yield


def build_unified_app() -> FastAPI:
    """Construct the parent FastAPI app with all mounts wired in order."""
    parent = FastAPI(
        lifespan=combined_lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # /v2/{path} at the ROOT — registered FIRST so the SPA catch-all can't shadow it.
    parent.include_router(ollama_registry_router)
    # /auth/start + /auth/callback at the ROOT. These are BROWSER redirect targets,
    # not XHR: OAUTH_REDIRECT_URI is configured as "<host>/auth/callback" (root) and
    # the IdP redirects the browser straight there, so the handler CANNOT live only
    # under /api (the redirect would hit the SPA catch-all → 404 after login). The
    # same router is also reachable under /api via gateway_app (the SPA kicks the
    # flow off at /api/auth/start); both paths share one self-contained handler.
    parent.include_router(oauth_router)
    parent.mount("/api", gateway_app)
    parent.mount("/inf", inference_app)
    dash = _dashboard_dir()
    if os.path.isdir(dash):
        # LAST: the "/" SPA catch-all. Guarded so importing this module is safe
        # even when the built dashboard dir is absent.
        parent.mount("/", SPAStaticFiles(directory=dash, html=True))
    return parent


app = build_unified_app()
