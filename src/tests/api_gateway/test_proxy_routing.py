"""Route-structure guards for the proxy_routes refactor.

Validates:
  1. The compute router prefix is /v1 (not /api/v1).
  2. The /v2 OCI mirror routes live on a dedicated ollama_registry_router, NOT on
     worker_passthrough_router.
  3. The /hf mirror route remains on worker_passthrough_router.
"""

from api_gateway.gateway import proxy_routes


def test_compute_router_prefix_is_v1():
    assert proxy_routes.router.prefix == "/v1"


def test_v2_on_dedicated_router_not_worker_passthrough():
    wp_paths = {getattr(r, "path", "") for r in proxy_routes.worker_passthrough_router.routes}
    assert not any(p.startswith("/v2") for p in wp_paths), "/v2 must leave worker_passthrough_router"
    oci_paths = {getattr(r, "path", "") for r in proxy_routes.ollama_registry_router.routes}
    assert any(p.startswith("/v2") for p in oci_paths)


def test_hf_stays_on_worker_passthrough():
    wp_paths = {getattr(r, "path", "") for r in proxy_routes.worker_passthrough_router.routes}
    assert any(p.startswith("/hf") for p in wp_paths)
