"""Unit tests for worker-hosted inference routing helpers.

A deploy whose model runs on an InferiaLLM worker (ollama/vllm on an EC2) is
reached via the worker's :8080 inference proxy at the deployment's endpoint
(advertise_url). That proxy (a) auths with the pool inference_token as the
bearer and (b) routes to the right model container by the
X-Inferia-Deployment-Id header. External providers (openai/groq/…) keep their
own api_key and get no such header.
"""
from __future__ import annotations

from inference.core.worker_routing import (
    provider_auth,
    upstream_model,
    envoy_route_headers,
)


def _dep(**over):
    base = {
        "id": "dep-123",
        "engine": "ollama",
        "model_name": "gemma-e2e",          # display name (resolve match)
        "configuration": {"model_id": "gemma3:4b"},  # the real ollama tag
        "inference_model": None,
        "inference_token": None,
    }
    base.update(over)
    return base


def test_worker_hosted_uses_inference_token_and_deployment_id_header():
    pk, extra = provider_auth(_dep(inference_token="tok-abc"), "ollama", "internal-key")
    assert pk == "tok-abc"
    assert extra["X-Inferia-Deployment-Id"] == "dep-123"


def test_external_engine_keeps_its_own_api_key_and_no_routing_header():
    pk, extra = provider_auth(
        _dep(engine="openai", configuration={"api_key": "sk-xx"}, inference_token=None),
        "openai", "internal-key",
    )
    assert pk == "sk-xx"
    assert "X-Inferia-Deployment-Id" not in extra


def test_non_external_no_token_falls_back_to_internal_key():
    pk, extra = provider_auth(_dep(inference_token=None), "vllm", "internal-key")
    assert pk == "internal-key"
    assert extra == {}


def test_inference_token_wins_even_if_a_stale_config_key_exists():
    # A worker deploy should never auth to its own proxy with a stray config
    # api_key — the pool token is authoritative.
    pk, extra = provider_auth(
        _dep(inference_token="tok-abc", configuration={"model_id": "gemma3:4b", "api_key": "ignored"}),
        "ollama", "internal-key",
    )
    assert pk == "tok-abc"
    assert extra["X-Inferia-Deployment-Id"] == "dep-123"


def test_upstream_model_resolution_order():
    assert upstream_model(_dep(inference_model="explicit")) == "explicit"
    # no inference_model → configuration.model_id (the ollama tag)
    assert upstream_model(_dep(inference_model=None)) == "gemma3:4b"
    # neither → model_name display fallback
    assert upstream_model(_dep(inference_model=None, configuration={})) == "gemma-e2e"


def test_envoy_route_headers_with_valid_pool_and_engine():
    deployment = _dep(pool_id="pool-123_abc.xyz", engine="vllm", inference_token="tok-123")
    url, headers = envoy_route_headers(deployment, "http://front-envoy:10000")
    assert url == "http://front-envoy:10000"
    # model_name="gemma-e2e" from _dep base → model-aware cluster
    assert headers["X-Inferia-Route-Cluster"] == "grp-pool-123_abc.xyz-vllm-gemma-e2e"


def test_envoy_route_headers_known_model_pooled():
    deployment = _dep(pool_id="pool-1", engine="vllm", inference_token="tok-123",
                      inference_model="llama3:8b")
    url, headers = envoy_route_headers(deployment, "http://front-envoy:10000")
    assert headers["X-Inferia-Route-Cluster"] == "grp-pool-1-vllm-llama3-8b"


def test_envoy_route_headers_known_model_singleton():
    deployment = _dep(pool_id=None, engine="vllm", inference_token="tok-123",
                      inference_model="llama3:8b")
    url, headers = envoy_route_headers(deployment, "http://front-envoy:10000")
    assert headers["X-Inferia-Route-Cluster"] == "grp-vllm-llama3-8b"


def test_envoy_route_headers_default_model_falls_back():
    deployment = _dep(pool_id=None, engine="vllm", inference_token="tok-123",
                      model_name=None, inference_model=None)
    url, headers = envoy_route_headers(deployment, "http://front-envoy:10000")
    assert url == "http://front-envoy:10000"
    assert headers["X-Inferia-Route-Cluster"] == "inferia-workers"


def test_envoy_route_headers_pooled_default_model():
    deployment = _dep(pool_id="pool-1", engine="vllm", inference_token="tok-123",
                      model_name=None, inference_model=None)
    url, headers = envoy_route_headers(deployment, "http://front-envoy:10000")
    assert headers["X-Inferia-Route-Cluster"] == "grp-pool-1-vllm"


def test_envoy_route_headers_no_envoy_url_returns_none():
    deployment = _dep(pool_id="pool-123", engine="vllm", inference_token="tok-123")
    url, headers = envoy_route_headers(deployment, None)
    assert url is None
    assert headers == {}


def test_envoy_route_headers_non_worker_hosted_returns_none():
    deployment = _dep(pool_id="pool-123", engine="vllm", inference_token=None)
    url, headers = envoy_route_headers(deployment, "http://front-envoy:10000")
    assert url is None
    assert headers == {}

