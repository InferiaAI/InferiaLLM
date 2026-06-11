"""Tests for /preflight hf_token_name resolution (T10).

These tests run entirely with mocked preflight helpers — no DB or network.
They assert that:
  1. hf_token_name is resolved to the raw token value and passed to
     check_model_accessibility / fetch_hf_model_info / check_model_format.
  2. An explicit hf_token takes precedence over hf_token_name.
  3. When hf_token_name resolves to None (token not found) the checks still
     run but with no token (same behaviour as before this change).
  4. PreflightRequest correctly accepts the new field.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from services.orchestration.model_deployment import deployment_server
from services.orchestration.model_deployment.deployment_server import (
    PreflightRequest,
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app():
    """Minimal FastAPI app with the deployment router (no DB needed for preflight)."""
    _app = FastAPI()
    _app.state.pool = None  # preflight doesn't hit the DB
    _app.state.worker_controller = AsyncMock()
    _app.state.event_bus = None
    _app.include_router(deployment_server.router)
    return _app


def _make_accessibility(accessible=True, skipped=False, needs_token=False, error=None):
    r = MagicMock()
    r.accessible = accessible
    r.skipped = skipped
    r.needs_token = needs_token
    r.error = error
    return r


def _make_hf_info():
    r = MagicMock()
    r.parameter_count = None  # skip VRAM check
    r.pipeline_tag = "text-generation"
    return r


def _make_fmt(compatible=True, skipped=False, error=None):
    r = MagicMock()
    r.compatible = compatible
    r.skipped = skipped
    r.error = error
    return r


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_preflight_request_accepts_hf_token_name():
    """PreflightRequest.hf_token_name is an accepted optional field."""
    req = PreflightRequest(model_id="meta-llama/Llama-3-8B", hf_token_name="my-token")
    assert req.hf_token_name == "my-token"
    assert req.hf_token is None


async def test_preflight_named_token_resolved_and_passed_to_checks(app):
    """hf_token_name is resolved and the resolved value reaches check_model_accessibility."""
    resolved = "hf_resolved_secret"

    with (
        patch(
            "services.orchestration.model_deployment.hf_token_resolver.resolve_hf_token",
            new_callable=AsyncMock,
            return_value=resolved,
        ) as mock_resolve,
        patch(
            "services.orchestration.model_deployment.preflight.check_model_accessibility",
            new_callable=AsyncMock,
            return_value=_make_accessibility(accessible=True),
        ) as mock_access,
        patch(
            "services.orchestration.model_deployment.preflight.fetch_hf_model_info",
            new_callable=AsyncMock,
            return_value=_make_hf_info(),
        ) as mock_info,
        patch(
            "services.orchestration.model_deployment.preflight.check_model_format",
            new_callable=AsyncMock,
            return_value=_make_fmt(),
        ) as mock_fmt,
        patch(
            "services.orchestration.model_deployment.preflight.check_vram_fit",
            return_value=MagicMock(skipped=True),
        ),
        patch(
            "services.orchestration.model_deployment.preflight.check_pipeline_compatibility",
            return_value=MagicMock(skipped=True),
        ),
        patch(
            "services.orchestration.model_deployment.preflight.check_docker_image_exists",
            new_callable=AsyncMock,
            return_value=MagicMock(exists=True, skipped=False),
        ),
        patch(
            "services.orchestration.model_deployment.preflight.check_duplicate_deployment",
            new_callable=AsyncMock,
            return_value=MagicMock(is_duplicate=False),
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/deployment/preflight",
                json={
                    "model_id": "meta-llama/Llama-3-8B",
                    "engine": "vllm",
                    "hf_token_name": "my-prod-token",
                },
            )

    assert resp.status_code == 200, resp.text
    mock_resolve.assert_called_once_with("my-prod-token")
    # The resolved value must be forwarded, not the raw req.hf_token (None)
    mock_access.assert_called_once()
    assert mock_access.call_args.args[1] == resolved, (
        f"check_model_accessibility received {mock_access.call_args.args[1]!r}, "
        f"expected resolved token {resolved!r}"
    )
    mock_info.assert_called_once()
    assert mock_info.call_args.args[1] == resolved
    mock_fmt.assert_called_once()
    assert mock_fmt.call_args.args[2] == resolved


async def test_preflight_explicit_hf_token_takes_priority_over_name(app):
    """When both hf_token and hf_token_name are supplied, hf_token wins (no resolve call)."""
    explicit = "hf_explicit"

    with (
        patch(
            "services.orchestration.model_deployment.hf_token_resolver.resolve_hf_token",
        ) as mock_resolve,
        patch(
            "services.orchestration.model_deployment.preflight.check_model_accessibility",
            new_callable=AsyncMock,
            return_value=_make_accessibility(accessible=True),
        ) as mock_access,
        patch(
            "services.orchestration.model_deployment.preflight.fetch_hf_model_info",
            new_callable=AsyncMock,
            return_value=_make_hf_info(),
        ),
        patch(
            "services.orchestration.model_deployment.preflight.check_model_format",
            new_callable=AsyncMock,
            return_value=_make_fmt(),
        ),
        patch(
            "services.orchestration.model_deployment.preflight.check_vram_fit",
            return_value=MagicMock(skipped=True),
        ),
        patch(
            "services.orchestration.model_deployment.preflight.check_pipeline_compatibility",
            return_value=MagicMock(skipped=True),
        ),
        patch(
            "services.orchestration.model_deployment.preflight.check_docker_image_exists",
            new_callable=AsyncMock,
            return_value=MagicMock(exists=True, skipped=False),
        ),
        patch(
            "services.orchestration.model_deployment.preflight.check_duplicate_deployment",
            new_callable=AsyncMock,
            return_value=MagicMock(is_duplicate=False),
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/deployment/preflight",
                json={
                    "model_id": "meta-llama/Llama-3-8B",
                    "engine": "vllm",
                    "hf_token": explicit,
                    "hf_token_name": "should-be-ignored",
                },
            )

    assert resp.status_code == 200, resp.text
    mock_resolve.assert_not_called()
    # Explicit token must be forwarded
    mock_access.assert_called_once()
    assert mock_access.call_args.args[1] == explicit


async def test_preflight_named_token_not_found_runs_without_token(app):
    """When hf_token_name resolves to None, checks still run (with token=None)."""
    with (
        patch(
            "services.orchestration.model_deployment.hf_token_resolver.resolve_hf_token",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_resolve,
        patch(
            "services.orchestration.model_deployment.preflight.check_model_accessibility",
            new_callable=AsyncMock,
            return_value=_make_accessibility(
                accessible=False, needs_token=True, error="requires token"
            ),
        ) as mock_access,
        patch(
            "services.orchestration.model_deployment.preflight.check_docker_image_exists",
            new_callable=AsyncMock,
            return_value=MagicMock(exists=True, skipped=False),
        ),
        patch(
            "services.orchestration.model_deployment.preflight.check_duplicate_deployment",
            new_callable=AsyncMock,
            return_value=MagicMock(is_duplicate=False),
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/deployment/preflight",
                json={
                    "model_id": "meta-llama/gated-model",
                    "engine": "vllm",
                    "hf_token_name": "nonexistent",
                },
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ready"] is False
    mock_resolve.assert_called_once_with("nonexistent")
    # check_model_accessibility called with None (resolved value)
    mock_access.assert_called_once()
    assert mock_access.call_args.args[1] is None


async def test_preflight_no_token_fields_runs_normally(app):
    """No hf_token or hf_token_name → resolve_hf_token never called, runs as before."""
    with (
        patch(
            "services.orchestration.model_deployment.hf_token_resolver.resolve_hf_token",
        ) as mock_resolve,
        patch(
            "services.orchestration.model_deployment.preflight.check_model_accessibility",
            new_callable=AsyncMock,
            return_value=_make_accessibility(accessible=True),
        ),
        patch(
            "services.orchestration.model_deployment.preflight.fetch_hf_model_info",
            new_callable=AsyncMock,
            return_value=_make_hf_info(),
        ),
        patch(
            "services.orchestration.model_deployment.preflight.check_model_format",
            new_callable=AsyncMock,
            return_value=_make_fmt(),
        ),
        patch(
            "services.orchestration.model_deployment.preflight.check_vram_fit",
            return_value=MagicMock(skipped=True),
        ),
        patch(
            "services.orchestration.model_deployment.preflight.check_pipeline_compatibility",
            return_value=MagicMock(skipped=True),
        ),
        patch(
            "services.orchestration.model_deployment.preflight.check_docker_image_exists",
            new_callable=AsyncMock,
            return_value=MagicMock(exists=True, skipped=False),
        ),
        patch(
            "services.orchestration.model_deployment.preflight.check_duplicate_deployment",
            new_callable=AsyncMock,
            return_value=MagicMock(is_duplicate=False),
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/deployment/preflight",
                json={"model_id": "meta-llama/Llama-3-8B", "engine": "vllm"},
            )

    assert resp.status_code == 200, resp.text
    mock_resolve.assert_not_called()
