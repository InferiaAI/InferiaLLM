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
