"""Registration + RBAC-mapping guard for the engine-AMI proxy route.

These tests operate at import level — no DB or running server is required.
They verify:
  (a) Both the bare path and the sub-path form are mounted under /api/v1.
  (b) Only GET + POST are allowed (no DELETE).
  (c) The handler body references DEPLOYMENT_CREATE and DEPLOYMENT_LIST.
  (d) The upstream path prefix ``v1/admin/aws/engine-ami`` is present.
"""

from __future__ import annotations

import inspect

from services.api_gateway.gateway import proxy_routes


def test_engine_ami_proxy_route_registered():
    """Both the bare and sub-path routes must be present on the router."""
    paths = {getattr(r, "path", None) for r in proxy_routes.router.routes}
    assert "/api/v1/admin/aws/engine-ami" in paths, (
        "Bare path /api/v1/admin/aws/engine-ami not registered"
    )
    assert "/api/v1/admin/aws/engine-ami/{path:path}" in paths, (
        "Sub-path /api/v1/admin/aws/engine-ami/{path:path} not registered"
    )


def test_engine_ami_proxy_methods_and_rbac():
    """The handler must allow GET+POST, forbid DELETE, and enforce DEPLOYMENT_LIST/CREATE."""
    routes = [
        r
        for r in proxy_routes.router.routes
        if getattr(r, "path", "") == "/api/v1/admin/aws/engine-ami/{path:path}"
    ]
    assert routes, "engine-ami sub-path route missing from router"

    methods: set[str] = set()
    for r in routes:
        methods |= set(getattr(r, "methods", set()) or set())

    assert {"GET", "POST"} <= methods, (
        f"Expected GET and POST to be registered; got {methods}"
    )
    assert "DELETE" not in methods, (
        "DELETE must NOT be registered on the engine-ami route"
    )

    src = inspect.getsource(proxy_routes.proxy_admin_engine_ami)
    assert "DEPLOYMENT_CREATE" in src, "DEPLOYMENT_CREATE RBAC check missing from handler"
    assert "DEPLOYMENT_LIST" in src, "DEPLOYMENT_LIST RBAC check missing from handler"
    assert "v1/admin/aws/engine-ami" in src, "upstream path prefix missing from handler"


def test_engine_ami_bare_route_methods():
    """The bare (no sub-path) route must also expose GET+POST and no DELETE."""
    routes = [
        r
        for r in proxy_routes.router.routes
        if getattr(r, "path", "") == "/api/v1/admin/aws/engine-ami"
    ]
    assert routes, "bare engine-ami route /api/v1/admin/aws/engine-ami missing from router"

    methods: set[str] = set()
    for r in routes:
        methods |= set(getattr(r, "methods", set()) or set())

    assert {"GET", "POST"} <= methods, (
        f"Bare route expected GET and POST; got {methods}"
    )
    assert "DELETE" not in methods, (
        "DELETE must NOT be registered on the bare engine-ami route"
    )


def test_engine_ami_handler_is_named_correctly():
    """The handler function must exist as ``proxy_admin_engine_ami`` in the module."""
    assert hasattr(proxy_routes, "proxy_admin_engine_ami"), (
        "proxy_routes.proxy_admin_engine_ami not found"
    )
    assert callable(proxy_routes.proxy_admin_engine_ami), (
        "proxy_routes.proxy_admin_engine_ami is not callable"
    )
