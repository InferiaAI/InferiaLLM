"""
Tests for CORS configuration in the Inference service.

Verifies that:
1. The inference app does NOT use a hardcoded wildcard origin in production
2. setup_cors correctly parses ALLOWED_ORIGINS
3. Development mode allows wildcard origins
"""

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from inferia.common.app_setup import setup_cors


def _get_cors_middleware(app: FastAPI):
    """Extract CORSMiddleware config from a FastAPI app."""
    for mw in app.user_middleware:
        if mw.cls is CORSMiddleware:
            return mw
    return None


class TestSetupCors:
    """Verify the shared setup_cors helper."""

    def test_production_uses_configured_origins(self):
        """In production mode, only configured origins are allowed."""
        app = FastAPI()
        setup_cors(app, "https://app.example.com,https://admin.example.com", is_development=False)

        cors_mw = _get_cors_middleware(app)
        assert cors_mw is not None
        assert cors_mw.kwargs["allow_origins"] == [
            "https://app.example.com",
            "https://admin.example.com",
        ]

    def test_production_empty_origins_means_no_origins(self):
        """Empty ALLOWED_ORIGINS in production means no origins allowed."""
        app = FastAPI()
        setup_cors(app, "", is_development=False)

        cors_mw = _get_cors_middleware(app)
        assert cors_mw is not None
        assert cors_mw.kwargs["allow_origins"] == []

    def test_development_allows_wildcard(self):
        """Development mode should use wildcard origins."""
        app = FastAPI()
        setup_cors(app, "https://app.example.com", is_development=True)

        cors_mw = _get_cors_middleware(app)
        assert cors_mw is not None
        assert cors_mw.kwargs["allow_origins"] == ["*"]

    def test_whitespace_in_origins_is_trimmed(self):
        """Whitespace around origins should be stripped."""
        app = FastAPI()
        setup_cors(app, "  https://a.com ,  https://b.com  ", is_development=False)

        cors_mw = _get_cors_middleware(app)
        assert cors_mw is not None
        assert cors_mw.kwargs["allow_origins"] == [
            "https://a.com",
            "https://b.com",
        ]

    def test_single_origin(self):
        """Single origin without commas should work."""
        app = FastAPI()
        setup_cors(app, "https://only.com", is_development=False)

        cors_mw = _get_cors_middleware(app)
        assert cors_mw is not None
        assert cors_mw.kwargs["allow_origins"] == ["https://only.com"]


class TestInferenceAppCors:
    """Verify the inference app wires CORS correctly (not wildcard)."""

    def test_inference_app_source_uses_setup_cors(self):
        """The inference app source must call setup_cors, not hardcode wildcard."""
        import pathlib

        app_path = pathlib.Path(__file__).resolve().parent.parent / "app.py"
        source = app_path.read_text()

        # Should NOT contain the old hardcoded pattern
        assert 'allow_origins=["*"]' not in source, (
            "Inference app still has hardcoded wildcard CORS origins"
        )
        # Should use the standardized setup_cors function
        assert "setup_cors(" in source

    def test_inference_app_source_no_direct_cors_middleware(self):
        """The inference app should not directly add CORSMiddleware."""
        import pathlib

        app_path = pathlib.Path(__file__).resolve().parent.parent / "app.py"
        source = app_path.read_text()

        assert "app.add_middleware(\n    CORSMiddleware" not in source
        assert "app.add_middleware(CORSMiddleware" not in source
