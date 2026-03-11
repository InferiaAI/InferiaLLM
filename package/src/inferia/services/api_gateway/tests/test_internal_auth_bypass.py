"""
Tests for internal endpoint auth bypass prevention.

Verifies that:
1. The /internal prefix match uses trailing slash to avoid matching unintended paths
2. Internal endpoints are explicitly rejected when INTERNAL_API_KEY is not configured
3. Valid API key grants access, invalid/missing key blocks access
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import AsyncClient, ASGITransport
import asyncio

from inferia.common.middleware import create_internal_auth_middleware


def _make_test_app(internal_api_key, check_path_prefix="/internal/"):
    """Create a minimal FastAPI app with the internal auth middleware."""
    app = FastAPI()

    middleware_fn = create_internal_auth_middleware(
        internal_api_key=internal_api_key,
        check_path_prefix=check_path_prefix,
    )
    app.middleware("http")(middleware_fn)

    @app.get("/internal/secret")
    async def internal_secret():
        return {"data": "secret"}

    @app.get("/internalized")
    async def internalized():
        return {"data": "not-internal"}

    @app.get("/public")
    async def public_route():
        return {"data": "public"}

    return app


class TestInternalPrefixMatch:
    """Verify trailing slash prevents matching unintended paths."""

    def test_internalized_path_not_blocked(self):
        """Path /internalized must NOT be caught by /internal/ prefix check."""
        app = _make_test_app(internal_api_key="test-key-32chars-aaaaaaaaaaaaaaaa")

        async def _run():
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                # /internalized should NOT require internal API key
                resp = await client.get("/internalized")
                assert resp.status_code == 200, (
                    f"/internalized was blocked by internal middleware: {resp.status_code}"
                )
                assert resp.json()["data"] == "not-internal"

        asyncio.get_event_loop().run_until_complete(_run())

    def test_internal_slash_path_requires_key(self):
        """Path /internal/secret MUST require the internal API key."""
        app = _make_test_app(internal_api_key="test-key-32chars-aaaaaaaaaaaaaaaa")

        async def _run():
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                # Without key → 401
                resp = await client.get("/internal/secret")
                assert resp.status_code == 401

                # With valid key → 200
                resp = await client.get(
                    "/internal/secret",
                    headers={"X-Internal-API-Key": "test-key-32chars-aaaaaaaaaaaaaaaa"},
                )
                assert resp.status_code == 200
                assert resp.json()["data"] == "secret"

        asyncio.get_event_loop().run_until_complete(_run())


class TestFailClosedWhenKeyNotConfigured:
    """Verify internal endpoints are blocked when INTERNAL_API_KEY is None/empty."""

    def test_none_key_returns_503(self):
        """When internal_api_key is None, internal endpoints return 503."""
        app = _make_test_app(internal_api_key=None)

        async def _run():
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/internal/secret",
                    headers={"X-Internal-API-Key": "anything"},
                )
                assert resp.status_code == 503
                assert "not configured" in resp.json()["detail"]

        asyncio.get_event_loop().run_until_complete(_run())

    def test_empty_key_returns_503(self):
        """When internal_api_key is empty string, internal endpoints return 503."""
        app = _make_test_app(internal_api_key="")

        async def _run():
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/internal/secret",
                    headers={"X-Internal-API-Key": "anything"},
                )
                assert resp.status_code == 503

        asyncio.get_event_loop().run_until_complete(_run())

    def test_non_internal_paths_still_work_when_key_not_configured(self):
        """Public paths must work even when internal_api_key is None."""
        app = _make_test_app(internal_api_key=None)

        async def _run():
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                resp = await client.get("/public")
                assert resp.status_code == 200
                assert resp.json()["data"] == "public"

        asyncio.get_event_loop().run_until_complete(_run())


class TestInternalKeyValidation:
    """Verify correct API key validation behavior."""

    def test_missing_key_returns_401(self):
        """No X-Internal-API-Key header → 401."""
        app = _make_test_app(internal_api_key="valid-key-32chars-aaaaaaaaaaaaaaaa")

        async def _run():
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                resp = await client.get("/internal/secret")
                assert resp.status_code == 401
                assert "Missing" in resp.json()["detail"]

        asyncio.get_event_loop().run_until_complete(_run())

    def test_wrong_key_returns_403(self):
        """Invalid X-Internal-API-Key header → 403."""
        app = _make_test_app(internal_api_key="valid-key-32chars-aaaaaaaaaaaaaaaa")

        async def _run():
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/internal/secret",
                    headers={"X-Internal-API-Key": "wrong-key"},
                )
                assert resp.status_code == 403
                assert "Invalid" in resp.json()["detail"]

        asyncio.get_event_loop().run_until_complete(_run())

    def test_valid_key_returns_200(self):
        """Correct X-Internal-API-Key header → 200."""
        app = _make_test_app(internal_api_key="valid-key-32chars-aaaaaaaaaaaaaaaa")

        async def _run():
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/internal/secret",
                    headers={"X-Internal-API-Key": "valid-key-32chars-aaaaaaaaaaaaaaaa"},
                )
                assert resp.status_code == 200

        asyncio.get_event_loop().run_until_complete(_run())

    def test_alternative_header_name_works(self):
        """X-Internal-Key (alternative) header should also work."""
        app = _make_test_app(internal_api_key="valid-key-32chars-aaaaaaaaaaaaaaaa")

        async def _run():
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/internal/secret",
                    headers={"X-Internal-Key": "valid-key-32chars-aaaaaaaaaaaaaaaa"},
                )
                assert resp.status_code == 200

        asyncio.get_event_loop().run_until_complete(_run())
