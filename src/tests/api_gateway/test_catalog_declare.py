"""Tests for declare_catalog and resolve_service_id.

Coverage:
  resolve_service_id:
  - Returns matching UUID from services list
  - Returns None when slug absent from list
  - Returns None on 500 response
  - Returns None on network error
  - Returns None on malformed body ({} or {"services": "nope"})
  - Returns None on empty base_url / empty admin_token

  declare_catalog (existing + new):
  - Happy path: GET /api/v1/services resolved, PUT /api/v1/services/{uuid}/catalog → True
  - Explicit service_id: only PUT, no GET → True
  - Resolution fails (services list 500) → False, no PUT issued
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

from api_gateway.rbac.catalog import CATALOG, to_declare_request
from api_gateway.rbac.catalog_declare import (
    declare_catalog,
    resolve_service_id,
)

_FAKE_UUID = "18796444-5076-4a29-832a-dba5f876cb56"
_SERVICES_RESPONSE = {"services": [{"id": _FAKE_UUID, "slug": "inferiallm", "name": "InferiaLLM"}]}


# ---------------------------------------------------------------------------
# resolve_service_id — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_returns_uuid_from_services_list():
    """GET /api/v1/services with Bearer header → returns matching UUID."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_SERVICES_RESPONSE)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await resolve_service_id("https://auth.example.com/", "tok123", client=client)
    await client.aclose()

    assert result == _FAKE_UUID
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/v1/services"
    assert captured["auth"] == "Bearer tok123"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_slug_absent():
    """If the slug is not in the list, return None."""
    response_body = {"services": [{"id": "some-uuid", "slug": "other-service"}]}

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response_body))
    )
    result = await resolve_service_id("https://auth.example.com", "tok", client=client)
    await client.aclose()

    assert result is None


@pytest.mark.asyncio
async def test_resolve_returns_none_on_500():
    """Non-2xx from services list → None."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text="oops"))
    )
    result = await resolve_service_id("https://auth.example.com", "tok", client=client)
    await client.aclose()

    assert result is None


@pytest.mark.asyncio
async def test_resolve_returns_none_on_network_error():
    """ConnectError → None, no raise."""
    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    result = await resolve_service_id("https://auth.example.com", "tok", client=client)
    await client.aclose()

    assert result is None


@pytest.mark.asyncio
async def test_resolve_returns_none_on_empty_body():
    """Malformed body {} → None."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    )
    result = await resolve_service_id("https://auth.example.com", "tok", client=client)
    await client.aclose()

    assert result is None


@pytest.mark.asyncio
async def test_resolve_returns_none_on_non_list_services():
    """Body with services=string (not list) → None."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"services": "nope"})
        )
    )
    result = await resolve_service_id("https://auth.example.com", "tok", client=client)
    await client.aclose()

    assert result is None


@pytest.mark.asyncio
async def test_resolve_guards_empty_base_url():
    """Empty base_url → None, no request."""
    request_made = False

    def handler(r: httpx.Request) -> httpx.Response:
        nonlocal request_made
        request_made = True
        return httpx.Response(200, json=_SERVICES_RESPONSE)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await resolve_service_id("", "tok", client=client)
    await client.aclose()

    assert result is None
    assert not request_made


@pytest.mark.asyncio
async def test_resolve_guards_empty_admin_token():
    """Empty admin_token → None, no request."""
    request_made = False

    def handler(r: httpx.Request) -> httpx.Response:
        nonlocal request_made
        request_made = True
        return httpx.Response(200, json=_SERVICES_RESPONSE)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await resolve_service_id("https://auth.example.com", "", client=client)
    await client.aclose()

    assert result is None
    assert not request_made


# ---------------------------------------------------------------------------
# declare_catalog — new tests (slug resolution + explicit service_id)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_declare_resolves_uuid_and_puts_catalog():
    """No service_id given → GET /api/v1/services → PUT /api/v1/services/{uuid}/catalog → True."""
    get_seen = False
    put_seen = False
    put_uuid_used: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_seen, put_seen
        if request.method == "GET" and request.url.path == "/api/v1/services":
            get_seen = True
            return httpx.Response(200, json=_SERVICES_RESPONSE)
        if request.method == "PUT":
            put_seen = True
            put_uuid_used.append(request.url.path)
            return httpx.Response(204)
        return httpx.Response(400, text="unexpected request")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await declare_catalog("https://auth.example.com", "tok123", client=client)
    await client.aclose()

    assert ok is True
    assert get_seen
    assert put_seen
    assert put_uuid_used[0] == f"/api/v1/services/{_FAKE_UUID}/catalog"


@pytest.mark.asyncio
async def test_declare_with_explicit_service_id_no_get():
    """Explicit service_id → only PUT, no GET to services list."""
    get_seen = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_seen
        if request.method == "GET":
            get_seen = True
            return httpx.Response(200, json=_SERVICES_RESPONSE)
        if request.method == "PUT":
            return httpx.Response(204)
        return httpx.Response(400)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await declare_catalog(
        "https://auth.example.com", "tok", service_id=_FAKE_UUID, client=client
    )
    await client.aclose()

    assert ok is True
    assert not get_seen, "should not have made the GET /api/v1/services request"


@pytest.mark.asyncio
async def test_declare_returns_false_when_resolution_fails():
    """Services list 500 → resolution fails → False, no PUT issued."""
    put_seen = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal put_seen
        if request.method == "GET":
            return httpx.Response(500, text="server error")
        if request.method == "PUT":
            put_seen = True
            return httpx.Response(204)
        return httpx.Response(400)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await declare_catalog("https://auth.example.com", "tok", client=client)
    await client.aclose()

    assert ok is False
    assert not put_seen, "PUT must not be issued when resolution fails"


# ---------------------------------------------------------------------------
# Happy path (updated to use explicit service_id to keep intent focused on PUT)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_declare_puts_catalog_with_bearer():
    """PUT to the correct UUID URL with Bearer auth and the catalog body; returns True."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            captured["url"] = str(request.url)
            captured["method"] = request.method
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"ok": True})
        # Answer the resolve GET so tests without service_id= also work.
        return httpx.Response(200, json=_SERVICES_RESPONSE)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await declare_catalog(
        "https://auth.example.com/", "tok123", service_id=_FAKE_UUID, client=client
    )
    await client.aclose()

    assert ok is True
    assert captured["method"] == "PUT"
    assert captured["url"] == f"https://auth.example.com/api/v1/services/{_FAKE_UUID}/catalog"
    assert captured["auth"] == "Bearer tok123"
    assert captured["body"] == to_declare_request(CATALOG)


@pytest.mark.asyncio
async def test_declare_201_is_success():
    """201 Created should also be treated as a successful declaration."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(201, json={"created": True}))
    )
    ok = await declare_catalog(
        "https://auth.example.com", "tok", service_id=_FAKE_UUID, client=client
    )
    await client.aclose()

    assert ok is True


@pytest.mark.asyncio
async def test_declare_trailing_slash_stripped():
    """Trailing slash on base_url must be stripped so the path is clean."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            captured["url"] = str(request.url)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await declare_catalog(
        "https://auth.example.com///", "tok", service_id=_FAKE_UUID, client=client
    )
    await client.aclose()

    assert ok is True
    assert captured["url"] == f"https://auth.example.com/api/v1/services/{_FAKE_UUID}/catalog"


# ---------------------------------------------------------------------------
# Error cases — all return False, never raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_declare_returns_false_on_5xx():
    """503 and other 5xx responses → False, no exception."""
    def handler(r: httpx.Request) -> httpx.Response:
        if r.method == "GET":
            return httpx.Response(200, json=_SERVICES_RESPONSE)
        return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await declare_catalog("https://auth.example.com", "tok", client=client)
    await client.aclose()

    assert ok is False


@pytest.mark.asyncio
async def test_declare_returns_false_on_4xx():
    """401 and other 4xx responses → False, no exception."""
    def handler(r: httpx.Request) -> httpx.Response:
        if r.method == "GET":
            return httpx.Response(200, json=_SERVICES_RESPONSE)
        return httpx.Response(401)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await declare_catalog("https://auth.example.com", "tok", client=client)
    await client.aclose()

    assert ok is False


@pytest.mark.asyncio
async def test_declare_returns_false_on_network_error():
    """ConnectError on PUT → False, no exception."""
    def handler(r: httpx.Request) -> httpx.Response:
        if r.method == "GET":
            return httpx.Response(200, json=_SERVICES_RESPONSE)
        raise httpx.ConnectError("down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await declare_catalog("https://auth.example.com", "tok", client=client)
    await client.aclose()

    assert ok is False


@pytest.mark.asyncio
async def test_declare_returns_false_on_timeout():
    """ReadTimeout on PUT → False, no exception."""
    def handler(r: httpx.Request) -> httpx.Response:
        if r.method == "GET":
            return httpx.Response(200, json=_SERVICES_RESPONSE)
        raise httpx.ReadTimeout("timed out", request=r)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
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
        if r.method == "GET":
            return httpx.Response(200, json=_SERVICES_RESPONSE)
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
