"""Tests for exception handlers — error handling layer."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from httpx import AsyncClient, ASGITransport

from inferia.common.errors import (
    BadRequestError,
    RateLimitError,
    APIError,
)
from inferia.common.exception_handlers import register_exception_handlers


def _build_app(debug=False):
    app = FastAPI(debug=debug)
    register_exception_handlers(app)

    @app.get("/api-error")
    async def raise_api_error():
        raise BadRequestError(message="bad input", details={"field": "name"})

    @app.get("/rate-limit")
    async def raise_rate_limit():
        raise RateLimitError(retry_after=120)

    @app.get("/unhandled")
    async def raise_unhandled():
        raise RuntimeError("something broke")

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    return app


@pytest.mark.asyncio
class TestAPIErrorHandler:
    """APIError handler returns correct response."""

    async def test_returns_correct_status_and_body(self):
        app = _build_app()
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api-error")
            assert resp.status_code == 400
            body = resp.json()
            assert body["success"] is False
            assert body["error"]["code"] == "BAD_REQUEST"
            assert body["error"]["message"] == "bad input"

    async def test_includes_request_id(self):
        app = _build_app()
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api-error")
            body = resp.json()
            assert "request_id" in body

    async def test_includes_custom_headers(self):
        app = _build_app()
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            resp = await client.get("/rate-limit")
            assert resp.status_code == 429
            assert resp.headers.get("retry-after") == "120"


@pytest.mark.asyncio
class TestValidationErrorHandler:
    """Validation error handler returns 422."""

    async def test_returns_422(self):
        app = _build_app()

        @app.get("/validate")
        async def validate(count: int):
            return {"count": count}

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            resp = await client.get("/validate?count=not-a-number")
            assert resp.status_code == 422
            body = resp.json()
            assert body["error"]["code"] == "VALIDATION_ERROR"
            assert "errors" in body["error"]["details"]


@pytest.mark.asyncio
class TestUnhandledExceptionHandler:
    """Unhandled exception handler sanitizes errors."""

    async def test_returns_500_generic_message(self):
        app = _build_app(debug=False)
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            resp = await client.get("/unhandled")
            assert resp.status_code == 500
            body = resp.json()
            assert body["error"]["code"] == "INTERNAL_ERROR"
            assert "something broke" not in body["error"]["message"]

    async def test_no_details_when_debug_false(self):
        app = _build_app(debug=False)
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            resp = await client.get("/unhandled")
            body = resp.json()
            assert body["error"]["details"] == {}

    async def test_includes_class_name_when_debug_true(self):
        """In debug mode, Starlette's ServerErrorMiddleware intercepts first,
        so we test the handler function directly instead."""
        from unittest.mock import MagicMock
        from inferia.common.exception_handlers import unhandled_exception_handler

        mock_request = MagicMock()
        mock_request.app.debug = True
        exc = RuntimeError("something broke")

        resp = await unhandled_exception_handler(mock_request, exc)
        body = resp.body.decode()
        import json
        data = json.loads(body)
        assert data["error"]["details"]["type"] == "RuntimeError"


@pytest.mark.asyncio
class TestRegisterHandlers:
    """register_exception_handlers installs all handlers."""

    async def test_handlers_installed(self):
        app = _build_app()
        # Check that exception handlers are registered
        assert APIError in app.exception_handlers
        assert RequestValidationError in app.exception_handlers
        assert Exception in app.exception_handlers
