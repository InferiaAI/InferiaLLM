"""
Tests for inferia.common.app_setup — shared FastAPI setup utilities.

Covers:
- setup_cors: origin parsing, production vs development mode, edge cases
- add_standard_health_routes: / and /health route behavior
"""

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import AsyncClient, ASGITransport

from inferia.common.app_setup import setup_cors, add_standard_health_routes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_cors_kwargs(app: FastAPI) -> dict:
    """Extract CORSMiddleware kwargs from a FastAPI app's middleware stack."""
    for mw in app.user_middleware:
        if mw.cls is CORSMiddleware:
            return mw.kwargs
    return {}


# ---------------------------------------------------------------------------
# setup_cors
# ---------------------------------------------------------------------------

class TestSetupCorsOriginParsing:
    """Verify comma-separated origin string is parsed correctly."""

    def test_multiple_origins(self):
        app = FastAPI()
        setup_cors(app, "https://a.com,https://b.com", is_development=False)
        assert _get_cors_kwargs(app)["allow_origins"] == [
            "https://a.com",
            "https://b.com",
        ]

    def test_single_origin(self):
        app = FastAPI()
        setup_cors(app, "https://only.com", is_development=False)
        assert _get_cors_kwargs(app)["allow_origins"] == ["https://only.com"]

    def test_whitespace_trimmed(self):
        app = FastAPI()
        setup_cors(app, "  https://a.com , https://b.com  ", is_development=False)
        assert _get_cors_kwargs(app)["allow_origins"] == [
            "https://a.com",
            "https://b.com",
        ]

    def test_trailing_comma_ignored(self):
        app = FastAPI()
        setup_cors(app, "https://a.com,", is_development=False)
        assert _get_cors_kwargs(app)["allow_origins"] == ["https://a.com"]

    def test_empty_segments_ignored(self):
        app = FastAPI()
        setup_cors(app, "https://a.com,,https://b.com", is_development=False)
        assert _get_cors_kwargs(app)["allow_origins"] == [
            "https://a.com",
            "https://b.com",
        ]

    def test_whitespace_only_segments_ignored(self):
        app = FastAPI()
        setup_cors(app, "https://a.com,   ,https://b.com", is_development=False)
        assert _get_cors_kwargs(app)["allow_origins"] == [
            "https://a.com",
            "https://b.com",
        ]


class TestSetupCorsEmptyAndMissing:
    """Verify behavior when no origins are provided."""

    def test_empty_string(self):
        app = FastAPI()
        setup_cors(app, "", is_development=False)
        assert _get_cors_kwargs(app)["allow_origins"] == []

    def test_only_commas(self):
        app = FastAPI()
        setup_cors(app, ",,,", is_development=False)
        assert _get_cors_kwargs(app)["allow_origins"] == []

    def test_only_whitespace(self):
        app = FastAPI()
        setup_cors(app, "   ", is_development=False)
        # "   ".strip() is empty, but split(",") yields ["   "] which strip() makes ""
        # The list comprehension filters it out
        assert _get_cors_kwargs(app)["allow_origins"] == []


class TestSetupCorsDevelopmentMode:
    """Development mode should always use wildcard regardless of configured origins."""

    def test_dev_mode_uses_wildcard(self):
        app = FastAPI()
        setup_cors(app, "https://specific.com", is_development=True)
        assert _get_cors_kwargs(app)["allow_origins"] == ["*"]

    def test_dev_mode_with_empty_origins_uses_wildcard(self):
        app = FastAPI()
        setup_cors(app, "", is_development=True)
        assert _get_cors_kwargs(app)["allow_origins"] == ["*"]

    def test_production_does_not_use_wildcard(self):
        app = FastAPI()
        setup_cors(app, "https://specific.com", is_development=False)
        origins = _get_cors_kwargs(app)["allow_origins"]
        assert "*" not in origins
        assert origins == ["https://specific.com"]


class TestSetupCorsMiddlewareSettings:
    """Verify credentials, methods, and headers settings."""

    def test_credentials_enabled(self):
        app = FastAPI()
        setup_cors(app, "https://a.com", is_development=False)
        assert _get_cors_kwargs(app)["allow_credentials"] is True

    def test_all_methods_allowed(self):
        app = FastAPI()
        setup_cors(app, "https://a.com", is_development=False)
        assert _get_cors_kwargs(app)["allow_methods"] == ["*"]

    def test_all_headers_allowed(self):
        app = FastAPI()
        setup_cors(app, "https://a.com", is_development=False)
        assert _get_cors_kwargs(app)["allow_headers"] == ["*"]


# ---------------------------------------------------------------------------
# add_standard_health_routes
# ---------------------------------------------------------------------------

class TestHealthRoutes:
    """Verify / and /health route responses."""

    @pytest.fixture
    def app_with_health(self):
        app = FastAPI()
        add_standard_health_routes(
            app=app,
            app_name="TestService",
            app_version="1.2.3",
            environment="testing",
        )
        return app

    @pytest.fixture
    def app_with_extra_components(self):
        app = FastAPI()
        add_standard_health_routes(
            app=app,
            app_name="TestService",
            app_version="1.2.3",
            environment="testing",
            extra_components={"database": "connected", "redis": "connected"},
        )
        return app

    @pytest.mark.asyncio
    async def test_root_returns_service_info(self, app_with_health):
        transport = ASGITransport(app=app_with_health)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "TestService"
        assert data["version"] == "1.2.3"
        assert data["environment"] == "testing"
        assert data["docs"] == "/docs"
        assert data["health"] == "/health"

    @pytest.mark.asyncio
    async def test_health_returns_healthy(self, app_with_health):
        transport = ASGITransport(app=app_with_health)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["version"] == "1.2.3"
        assert data["service"] == "TestService"
        assert data["components"] == {}

    @pytest.mark.asyncio
    async def test_health_with_extra_components(self, app_with_extra_components):
        transport = ASGITransport(app=app_with_extra_components)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["components"] == {
            "database": "connected",
            "redis": "connected",
        }

    @pytest.mark.asyncio
    async def test_health_without_extra_components_defaults_empty(self, app_with_health):
        transport = ASGITransport(app=app_with_health)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
        assert resp.json()["components"] == {}
