"""What the gateway proxy may and may not forward to orchestration.

The proxy attaches the internal API key and the caller's identity headers, so
a request must not be able to leave its route's prefix or choose its own org.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from api_gateway.gateway import proxy_routes


@pytest.fixture
def upstream():
    """A proxy client that records each request instead of sending it."""
    client = MagicMock()
    client.request = AsyncMock(
        return_value=httpx.Response(200, json={}, headers={"content-type": "application/json"})
    )
    with patch.object(
        proxy_routes.gateway_http_client, "get_proxy_client", return_value=client
    ):
        yield client


def _sent(upstream):
    kwargs = upstream.request.call_args.kwargs
    return kwargs["url"], {k.lower(): v for k, v in kwargs["headers"].items()}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/v1/workers/%2e%2e/%2e%2e/deployment/deletepool/p1",
    "/v1/workers/%2e/register",
    "/v1/llmfit/%2e%2e/admin",
])
async def test_unauthenticated_routes_reject_dot_segments(client, upstream, path):
    r = await client.post(path) if "workers" in path else await client.get(path)

    assert r.status_code == 400
    upstream.request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/v1/providers/%2e%2e/%2e%2e/%2e%2e/v1/admin/workers/x",
    "/v1/deployment/%2e%2e/v1/models/import",
    "/v1/nodes/%2e%2e/%2e%2e/deployment/deletepool/p1",
])
async def test_authenticated_routes_reject_dot_segments(client, upstream, admin_token, path):
    r = await client.request(
        "GET" if "providers" in path else "POST", path,
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert r.status_code == 400
    upstream.request.assert_not_called()


@pytest.mark.asyncio
async def test_caller_cannot_choose_the_forwarded_org(client, upstream, admin_token):
    await client.get(
        "/v1/deployments/list",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "X-Organization-ID": "someone-elses-org",
            "X-User-ID": "someone-else",
            "X-Internal-API-Key": "guess",
        },
    )

    _, headers = _sent(upstream)
    assert headers["x-organization-id"] == "org_default"
    assert headers["x-user-id"] == "user_admin_001"
    assert headers["x-internal-api-key"] == proxy_routes.settings.internal_api_key
    # One value each: orchestration reads the first of a repeated header.
    names = [k.lower() for k in upstream.request.call_args.kwargs["headers"]]
    assert names.count("x-organization-id") == 1
    assert "authorization" not in headers


@pytest.mark.asyncio
async def test_worker_passthrough_keeps_only_the_worker_token(client, upstream):
    await client.post(
        "/v1/workers/register",
        json={},
        headers={
            "Authorization": "Bearer bootstrap-jwt",
            "X-Organization-ID": "someone-elses-org",
            "X-User-ID": "someone-else",
        },
    )

    url, headers = _sent(upstream)
    assert url.endswith("/v1/workers/register")
    assert headers["authorization"] == "Bearer bootstrap-jwt"
    assert "x-organization-id" not in headers
    assert "x-user-id" not in headers


@pytest.mark.asyncio
async def test_only_the_provider_list_is_proxied_from_inventory(client, upstream, admin_token):
    auth = {"Authorization": f"Bearer {admin_token}"}

    ok = await client.get("/v1/inventory/providers", headers=auth)
    heartbeat = await client.post("/v1/inventory/heartbeat", json={}, headers=auth)
    nodes = await client.get("/v1/inventory/nodes/nosana", headers=auth)

    assert ok.status_code == 200
    assert heartbeat.status_code in (404, 405)
    assert nodes.status_code in (404, 405)
    assert upstream.request.call_count == 1
