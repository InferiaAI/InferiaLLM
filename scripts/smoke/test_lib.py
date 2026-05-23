"""Tests for scripts.smoke.lib — uses respx to mock all HTTP calls."""
from __future__ import annotations

import httpx
import pytest
import respx

from scripts.smoke.lib import (
    APIError,
    SmokeAPI,
)


BASE = "http://test"


@pytest.fixture
def api() -> SmokeAPI:
    return SmokeAPI(base_url=BASE)


@respx.mock
def test_login_stores_token(api: SmokeAPI) -> None:
    respx.post(f"{BASE}/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-123"})
    )
    api.login("admin@example.com", "pw")
    assert api._token == "tok-123"


@respx.mock
def test_login_propagates_4xx(api: SmokeAPI) -> None:
    respx.post(f"{BASE}/v1/auth/login").mock(
        return_value=httpx.Response(401, json={"detail": "bad creds"})
    )
    with pytest.raises(APIError) as exc:
        api.login("admin@example.com", "wrong")
    assert exc.value.status == 401


@respx.mock
def test_create_pool_returns_id(api: SmokeAPI) -> None:
    api._token = "t"
    respx.post(f"{BASE}/v1/compute-pools").mock(
        return_value=httpx.Response(200, json={"id": "pool-abc"})
    )
    pid = api.create_pool(provider="worker", name="smoke-local-1")
    assert pid == "pool-abc"


@respx.mock
def test_create_pool_includes_instance_type_metadata(api: SmokeAPI) -> None:
    api._token = "t"
    route = respx.post(f"{BASE}/v1/compute-pools").mock(
        return_value=httpx.Response(200, json={"id": "p"})
    )
    api.create_pool(
        provider="aws",
        name="smoke-aws-1",
        instance_type="g4dn.xlarge",
        metadata={"subnet_id": "subnet-abc", "worker_image_tag": "smoke-1"},
    )
    sent = route.calls.last.request.read()
    assert b"g4dn.xlarge" in sent
    assert b"subnet-abc" in sent


@respx.mock
def test_destroy_pool_idempotent_on_404(api: SmokeAPI) -> None:
    api._token = "t"
    respx.post(f"{BASE}/v1/compute-pools/p1:destroy").mock(
        return_value=httpx.Response(404, json={"detail": "gone"})
    )
    api.destroy_pool("p1")


@respx.mock
def test_mint_bootstrap_token(api: SmokeAPI) -> None:
    api._token = "t"
    respx.post(f"{BASE}/v1/admin/workers/mint").mock(
        return_value=httpx.Response(200, json={"token": "bt-xyz", "expires_at": "2099-01-01T00:00:00Z"}),
    )
    r = api.mint_bootstrap_token("pool-1", ttl_hours=1)
    assert r["token"] == "bt-xyz"


@pytest.mark.parametrize("ttl", [0, 25, -1])
def test_mint_bootstrap_token_rejects_bad_ttl(api: SmokeAPI, ttl: int) -> None:
    with pytest.raises(ValueError):
        api.mint_bootstrap_token("pool-1", ttl_hours=ttl)


@respx.mock
def test_list_workers(api: SmokeAPI) -> None:
    api._token = "t"
    respx.get(f"{BASE}/v1/admin/workers").mock(
        return_value=httpx.Response(
            200,
            json={"workers": [{"node_id": "n1", "status": "ready"}]},
        )
    )
    workers = api.list_workers("pool-1")
    assert workers == [{"node_id": "n1", "status": "ready"}]


@respx.mock
def test_create_deployment_returns_id(api: SmokeAPI) -> None:
    api._token = "t"
    route = respx.post(f"{BASE}/v1/deployments").mock(
        return_value=httpx.Response(200, json={"deployment_id": "dep-1"})
    )
    did = api.create_deployment(
        pool_id="p1",
        recipe="vllm",
        model_uri="hf://Qwen/Qwen3-0.6B",
        name="smoke-vllm",
        config={"gpu_memory_utilization": 0.5},
    )
    assert did == "dep-1"
    body = route.calls.last.request.read()
    assert b"Qwen3-0.6B" in body
    assert b"gpu_memory_utilization" in body


@respx.mock
def test_delete_deployment_tolerates_404(api: SmokeAPI) -> None:
    api._token = "t"
    respx.delete(f"{BASE}/v1/deployments/dep-1").mock(
        return_value=httpx.Response(404, json={"detail": "gone"})
    )
    api.delete_deployment("dep-1")


@respx.mock
def test_get_deployment(api: SmokeAPI) -> None:
    api._token = "t"
    respx.get(f"{BASE}/v1/deployments/dep-1").mock(
        return_value=httpx.Response(200, json={"id": "dep-1", "state": "running"})
    )
    d = api.get_deployment("dep-1")
    assert d["state"] == "running"
