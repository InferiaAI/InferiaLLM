"""Tests for scripts.smoke.lib — uses respx to mock all HTTP calls."""
from __future__ import annotations

import httpx
import pytest
import respx

from scripts.smoke.lib import (
    APIError,
    EmptyResponseError,
    SmokeAPI,
    SmokeTimeoutError,
    StreamTruncatedError,
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


@respx.mock
def test_chat_non_stream(api: SmokeAPI) -> None:
    api._token = "t"
    respx.post(f"{BASE}/v1/inference/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "hello there"}}]},
        )
    )
    assert api.chat("dep-1", "say hi") == "hello there"


@respx.mock
def test_chat_non_stream_empty_raises(api: SmokeAPI) -> None:
    api._token = "t"
    respx.post(f"{BASE}/v1/inference/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": ""}}]},
        )
    )
    with pytest.raises(EmptyResponseError):
        api.chat("dep-1", "say hi")


@respx.mock
def test_chat_stream_concatenates(api: SmokeAPI) -> None:
    api._token = "t"
    body = (
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post(f"{BASE}/v1/inference/chat/completions").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}),
    )
    assert api.chat("dep-1", "hi", stream=True) == "Hello"


@respx.mock
def test_chat_stream_missing_done_raises(api: SmokeAPI) -> None:
    api._token = "t"
    body = 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
    respx.post(f"{BASE}/v1/inference/chat/completions").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}),
    )
    with pytest.raises(StreamTruncatedError):
        api.chat("dep-1", "hi", stream=True)


def test_wait_until_returns_first_truthy() -> None:
    from scripts.smoke.lib import wait_until
    calls = {"n": 0}
    def p() -> str | None:
        calls["n"] += 1
        return "ok" if calls["n"] >= 3 else None
    assert wait_until(p, timeout=1.0, interval=0.01) == "ok"
    assert calls["n"] == 3


def test_wait_until_times_out() -> None:
    from scripts.smoke.lib import wait_until
    with pytest.raises(SmokeTimeoutError):
        wait_until(lambda: None, timeout=0.05, interval=0.01)


def test_wait_until_tolerates_503(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.smoke.lib import wait_until
    calls = {"n": 0}
    def p() -> str | None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise APIError(503, "")
        return "ok"
    assert wait_until(p, timeout=1.0, interval=0.01) == "ok"


def test_wait_until_propagates_4xx() -> None:
    from scripts.smoke.lib import wait_until
    def p() -> str | None:
        raise APIError(404, "")
    with pytest.raises(APIError):
        wait_until(p, timeout=1.0, interval=0.01)


def test_cost_estimate() -> None:
    from scripts.smoke.lib import cost_estimate
    s = cost_estimate("g4dn.xlarge", 0.083)
    assert "g4dn.xlarge" in s
    assert "$" in s


# ---- Extra coverage tests ----

def test_close_releases_client(api: SmokeAPI) -> None:
    """close() should close and null out an existing client."""
    # Force client creation
    _ = api._http()
    assert api._client is not None
    api.close()
    assert api._client is None
    # Calling close again on a None client should be a no-op
    api.close()


@respx.mock
def test_create_pool_with_region(api: SmokeAPI) -> None:
    """region parameter is included in POST body when provided."""
    api._token = "t"
    route = respx.post(f"{BASE}/v1/compute-pools").mock(
        return_value=httpx.Response(200, json={"id": "p"})
    )
    api.create_pool(provider="aws", name="smoke-aws", region="us-east-1")
    sent = route.calls.last.request.read()
    assert b"us-east-1" in sent


@respx.mock
def test_destroy_pool_propagates_non_404(api: SmokeAPI) -> None:
    """destroy_pool re-raises non-404 errors."""
    api._token = "t"
    respx.post(f"{BASE}/v1/compute-pools/p1:destroy").mock(
        return_value=httpx.Response(500, json={"detail": "server error"})
    )
    with pytest.raises(APIError) as exc:
        api.destroy_pool("p1")
    assert exc.value.status == 500


@respx.mock
def test_delete_deployment_propagates_non_404(api: SmokeAPI) -> None:
    """delete_deployment re-raises non-404 errors."""
    api._token = "t"
    respx.delete(f"{BASE}/v1/deployments/dep-1").mock(
        return_value=httpx.Response(500, json={"detail": "server error"})
    )
    with pytest.raises(APIError) as exc:
        api.delete_deployment("dep-1")
    assert exc.value.status == 500


@respx.mock
def test_chat_non_stream_4xx_raises(api: SmokeAPI) -> None:
    """chat() raises APIError for 4xx non-stream responses."""
    api._token = "t"
    respx.post(f"{BASE}/v1/inference/chat/completions").mock(
        return_value=httpx.Response(429, json={"detail": "rate limited"})
    )
    with pytest.raises(APIError) as exc:
        api.chat("dep-1", "hi")
    assert exc.value.status == 429


@respx.mock
def test_chat_stream_4xx_raises(api: SmokeAPI) -> None:
    """chat() raises APIError for 4xx streaming responses."""
    api._token = "t"
    respx.post(f"{BASE}/v1/inference/chat/completions").mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )
    with pytest.raises(APIError) as exc:
        api.chat("dep-1", "hi", stream=True)
    assert exc.value.status == 401


@respx.mock
def test_chat_stream_skips_invalid_json(api: SmokeAPI) -> None:
    """chat() stream skips malformed SSE data lines and still completes."""
    api._token = "t"
    body = (
        "data: not-valid-json\n\n"
        'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post(f"{BASE}/v1/inference/chat/completions").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}),
    )
    assert api.chat("dep-1", "hi", stream=True) == "ok"


@respx.mock
def test_chat_stream_empty_content_raises(api: SmokeAPI) -> None:
    """chat() stream with [DONE] but no delta content raises EmptyResponseError."""
    api._token = "t"
    body = "data: [DONE]\n\n"
    respx.post(f"{BASE}/v1/inference/chat/completions").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}),
    )
    with pytest.raises(EmptyResponseError):
        api.chat("dep-1", "hi", stream=True)
