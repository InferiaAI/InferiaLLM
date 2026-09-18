"""Route-structure guards for the proxy_routes refactor.

Validates:
  1. The compute router prefix is /v1 (not /api/v1).
  2. The model-cache era mirror passthroughs (/hf on worker_passthrough_router,
     the /v2 OCI registry router) are GONE — engine nodes pull model weights
     straight from origin (huggingface.co / registry), so the gateway must not
     expose unauthenticated streaming passthrough surfaces anymore.
"""

from api_gateway.gateway import proxy_routes


def test_compute_router_prefix_is_v1():
    assert proxy_routes.router.prefix == "/v1"


def test_hf_mirror_route_removed():
    wp_paths = {getattr(r, "path", "") for r in proxy_routes.worker_passthrough_router.routes}
    assert not any(p.startswith("/hf") for p in wp_paths), "/hf mirror passthrough must be gone"


def test_oci_registry_router_removed():
    assert not hasattr(proxy_routes, "ollama_registry_router")


def test_models_cache_proxy_routes_removed():
    paths = {getattr(r, "path", "") for r in proxy_routes.router.routes}
    assert "/v1/models" not in paths
    assert not any(p.startswith("/v1/models/") for p in paths)
