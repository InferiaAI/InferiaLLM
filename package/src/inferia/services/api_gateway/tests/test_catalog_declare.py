"""Tests for declare_catalog — HTTP client that PUTs InferiaLLM's catalog to InferiaAuth.

Coverage:
  - Happy path: correct URL, method, Authorization header, JSON body, returns True on 2xx
  - 4xx response → False (no raise)
  - 5xx response → False (no raise)
  - Network error (ConnectError) → False (no raise)
  - Timeout → False (no raise)
  - Empty base_url → False (no request)
  - Empty admin_token → False (no request)
  - Absurdly long admin_token (> 8192 chars) → False (no request)
  - Trailing slash stripped from base_url
  - 201 Created is treated as success
  - Client provided is used and NOT closed by declare_catalog
  - Client not provided → a temporary one is created internally
"""

from __future__ import annotations

import json

import httpx
import pytest

from inferia.services.api_gateway.rbac.catalog import CATALOG, to_declare_request
from inferia.services.api_gateway.rbac.catalog_declare import declare_catalog

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_declare_puts_catalog_with_bearer():
    """PUT to the correct URL with Bearer auth and the catalog body; returns True."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await declare_catalog("https://auth.example.com/", "tok123", client=client)
    await client.aclose()

    assert ok is True
    assert captured["method"] == "PUT"
    assert captured["url"] == "https://auth.example.com/api/v1/services/inferiallm/catalog"
    assert captured["auth"] == "Bearer tok123"
    assert captured["body"] == to_declare_request(CATALOG)


@pytest.mark.asyncio
async def test_declare_201_is_success():
    """201 Created should also be treated as a successful declaration."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"created": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await declare_catalog("https://auth.example.com", "tok", client=client)
    await client.aclose()

    assert ok is True


@pytest.mark.asyncio
async def test_declare_trailing_slash_stripped():
    """Trailing slash on base_url must be stripped so the path is clean."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await declare_catalog("https://auth.example.com///", "tok", client=client)
    await client.aclose()

    assert ok is True
    assert captured["url"] == "https://auth.example.com/api/v1/services/inferiallm/catalog"


# ---------------------------------------------------------------------------
# Error cases — all return False, never raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_declare_returns_false_on_5xx():
    """503 and other 5xx responses → False, no exception."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    ok = await declare_catalog("https://auth.example.com", "tok", client=client)
    await client.aclose()

    assert ok is False


@pytest.mark.asyncio
async def test_declare_returns_false_on_4xx():
    """401 and other 4xx responses → False, no exception."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    ok = await declare_catalog("https://auth.example.com", "tok", client=client)
    await client.aclose()

    assert ok is False


@pytest.mark.asyncio
async def test_declare_returns_false_on_network_error():
    """ConnectError → False, no exception."""
    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    ok = await declare_catalog("https://auth.example.com", "tok", client=client)
    await client.aclose()

    assert ok is False


@pytest.mark.asyncio
async def test_declare_returns_false_on_timeout():
    """ReadTimeout → False, no exception."""
    def timeout_transport(r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=r)

    client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_transport))
    ok = await declare_catalog("https://auth.example.com", "tok", client=client)
    await client.aclose()

    assert ok is False


# ---------------------------------------------------------------------------
# Input guards — return False before making any request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_declare_guards_empty_base_url():
    """Empty base_url → False, no request made."""
    request_made = False

    def handler(r: httpx.Request) -> httpx.Response:
        nonlocal request_made
        request_made = True
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await declare_catalog("", "tok", client=client)
    await client.aclose()

    assert ok is False
    assert not request_made


@pytest.mark.asyncio
async def test_declare_guards_empty_admin_token():
    """Empty admin_token → False, no request made."""
    request_made = False

    def handler(r: httpx.Request) -> httpx.Response:
        nonlocal request_made
        request_made = True
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await declare_catalog("https://x", "", client=client)
    await client.aclose()

    assert ok is False
    assert not request_made


@pytest.mark.asyncio
async def test_declare_guards_oversized_token():
    """admin_token > 8192 chars → False, no request made."""
    request_made = False

    def handler(r: httpx.Request) -> httpx.Response:
        nonlocal request_made
        request_made = True
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await declare_catalog("https://x", "a" * 8193, client=client)
    await client.aclose()

    assert ok is False
    assert not request_made


@pytest.mark.asyncio
async def test_declare_guards_combined_empty_inputs():
    """Convenience: both empty inputs at once → both False without requests."""
    assert await declare_catalog("", "tok") is False
    assert await declare_catalog("https://x", "") is False


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_declare_does_not_close_provided_client():
    """When the caller passes a client, declare_catalog must NOT close it."""
    def handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await declare_catalog("https://auth.example.com", "tok", client=client)

    assert not client.is_closed, "declare_catalog must not close the caller's client"
    await client.aclose()  # cleanup


@pytest.mark.asyncio
async def test_declare_works_without_provided_client():
    """When no client is provided, declare_catalog should create its own and succeed."""
    # We can only verify it doesn't raise; we can't inspect the internal client.
    # Use a real base_url that will fail — expect False (network error), no exception.
    ok = await declare_catalog("http://127.0.0.1:1", "tok")
    assert ok is False
