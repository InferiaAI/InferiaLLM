"""Tests for internal auth middleware — security layer."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI, Request
from httpx import AsyncClient, ASGITransport

from inferia.common.middleware import create_internal_auth_middleware
from inferia.common.http_client import request_id_ctx


def _build_app(api_key, check_path_prefix=None, skip_paths=None):
    """Build a minimal FastAPI app with the middleware under test."""
    app = FastAPI()
    middleware = create_internal_auth_middleware(
        internal_api_key=api_key,
        check_path_prefix=check_path_prefix,
        skip_paths=skip_paths,
    )
    app.middleware("http")(middleware)

    @app.get("/internal/data")
    async def protected():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/public")
    async def public():
        return {"public": True}

    return app


@pytest.mark.asyncio
class TestMiddlewareSecurity:
    """Verify internal auth middleware security properties."""

    async def test_missing_api_key_returns_401(self):
        app = _build_app("secret-key-123")
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            resp = await client.get("/internal/data")
            assert resp.status_code == 401
            assert "Missing" in resp.json()["detail"]

    async def test_invalid_api_key_returns_403(self):
        app = _build_app("secret-key-123")
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/internal/data", headers={"X-Internal-API-Key": "wrong-key"}
            )
            assert resp.status_code == 403
            assert "Invalid" in resp.json()["detail"]

    async def test_valid_api_key_passes_through(self):
        app = _build_app("secret-key-123")
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/internal/data", headers={"X-Internal-API-Key": "secret-key-123"}
            )
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

    async def test_unconfigured_key_returns_503(self):
        """Fail closed when INTERNAL_API_KEY is empty/None."""
        app = _build_app("")
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/internal/data", headers={"X-Internal-API-Key": "anything"}
            )
            assert resp.status_code == 503

    async def test_skip_paths_bypass_auth(self):
        app = _build_app("secret-key-123", skip_paths=["/health"])
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            resp = await client.get("/health")
            assert resp.status_code == 200

    async def test_path_prefix_matching(self):
        """Only paths starting with prefix are checked."""
        app = _build_app("secret-key-123", check_path_prefix="/internal/")
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            # /public does NOT match prefix -> passes without key
            resp = await client.get("/public")
            assert resp.status_code == 200

            # /internal/data DOES match prefix -> requires key
            resp = await client.get("/internal/data")
            assert resp.status_code == 401

    async def test_request_id_generated_in_response(self):
        app = _build_app("secret-key-123")
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/internal/data", headers={"X-Internal-API-Key": "secret-key-123"}
            )
            assert "x-request-id" in resp.headers
            # UUID format: 8-4-4-4-12
            rid = resp.headers["x-request-id"]
            assert len(rid) == 36

    async def test_request_id_context_reset_on_exception(self):
        """Context variable must be reset even if handler raises."""
        app = FastAPI()
        middleware = create_internal_auth_middleware(
            internal_api_key="key", skip_paths=["/boom"]
        )
        app.middleware("http")(middleware)

        @app.get("/boom")
        async def boom():
            raise RuntimeError("boom")

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            await client.get("/boom")
            # After the request, context should be reset to default
            assert request_id_ctx.get() is None

    async def test_custom_header_name_recognized(self):
        """X-Internal-Key (alternate header) also works."""
        app = _build_app("secret-key-123")
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/internal/data", headers={"X-Internal-Key": "secret-key-123"}
            )
            assert resp.status_code == 200

    async def test_concurrent_requests_get_independent_ids(self):
        app = _build_app("key", skip_paths=["/health"])
        collected_ids = []

        @app.get("/capture-id")
        async def capture():
            rid = request_id_ctx.get()
            return {"id": rid}

        # Need to also skip /capture-id or provide key
        app2 = _build_app("key", skip_paths=["/capture-id"])

        @app2.get("/capture-id")
        async def capture2():
            rid = request_id_ctx.get()
            return {"id": rid}

        async with AsyncClient(
            transport=ASGITransport(app=app2, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            r1 = await client.get("/capture-id")
            r2 = await client.get("/capture-id")
            id1 = r1.headers.get("x-request-id")
            id2 = r2.headers.get("x-request-id")
            assert id1 != id2
