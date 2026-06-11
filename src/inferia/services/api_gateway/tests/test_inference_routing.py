"""Tests for inference context resolution and routing — Layer 3."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestResolveInferenceContext:
    """resolve_inference_context endpoint logic."""

    @pytest.mark.asyncio
    async def test_invalid_api_key_returns_not_valid(self):
        """Invalid API key returns valid=False response (not a 4xx raise)."""
        from inferia.services.api_gateway.gateway.router import (
            resolve_inference_context,
            ResolveContextRequest,
        )

        request = ResolveContextRequest(api_key="bad-key", model="llama-3")

        with patch(
            "inferia.services.api_gateway.gateway.router.policy_engine"
        ) as mock_pe:
            mock_pe.resolve_context = AsyncMock(
                return_value={"valid": False, "error": "Invalid API Key"}
            )
            mock_db = AsyncMock()
            response = await resolve_inference_context(request, mock_db)
            assert response.valid is False
            assert "Invalid" in (response.error or "")

    @pytest.mark.asyncio
    async def test_valid_key_returns_deployment_context(self):
        """Valid key returns deployment configuration."""
        from inferia.services.api_gateway.gateway.router import (
            resolve_inference_context,
            ResolveContextRequest,
        )

        request = ResolveContextRequest(api_key="sk-valid-key", model="llama-3")

        resolved = {
            "valid": True,
            "deployment": {
                "id": "dep-001",
                "model_name": "llama-3",
                "endpoint": "http://llama:8080",
                "engine": "vllm",
                "configuration": "{}",
                "inference_model": "meta-llama/Llama-3-8b",
                "org_id": "org-001",
            },
            "config": {
                "rate_limit": None,
            },
            "user_id_context": "user-001",
            "log_payloads": True,
        }

        with patch(
            "inferia.services.api_gateway.gateway.router.policy_engine"
        ) as mock_pe:
            mock_pe.resolve_context = AsyncMock(return_value=resolved)
            mock_db = AsyncMock()
            response = await resolve_inference_context(request, mock_db)
            assert response.valid is True
            assert response.deployment["model_name"] == "llama-3"
            assert response.deployment["endpoint"] == "http://llama:8080"

    @pytest.mark.asyncio
    async def test_rate_limit_config_forwarded(self):
        """rate_limit configuration is forwarded in the resolved context."""
        from inferia.services.api_gateway.gateway.router import (
            resolve_inference_context,
            ResolveContextRequest,
        )

        request = ResolveContextRequest(api_key="sk-valid-key", model="llama-3")

        resolved = {
            "valid": True,
            "deployment": {
                "id": "dep-001",
                "model_name": "llama-3",
                "endpoint": "http://llama:8080",
                "engine": "vllm",
                "configuration": "{}",
                "inference_model": None,
                "org_id": "org-001",
            },
            "config": {
                "rate_limit": {"enabled": True, "rpm": 60},
            },
            "user_id_context": "user-001",
            "log_payloads": False,
        }

        with patch(
            "inferia.services.api_gateway.gateway.router.policy_engine"
        ) as mock_pe:
            mock_pe.resolve_context = AsyncMock(return_value=resolved)
            mock_db = AsyncMock()
            response = await resolve_inference_context(request, mock_db)
            assert response.rate_limit_config["enabled"] is True
            assert response.rate_limit_config["rpm"] == 60


class TestModelsList:
    """models list endpoint auth enforcement."""

    @pytest.mark.asyncio
    async def test_missing_bearer_token_returns_401(self):
        """Models list without Bearer token returns 401."""
        from inferia.services.api_gateway.gateway.router import list_models
        from fastapi import HTTPException

        mock_request = MagicMock()
        mock_request.headers = {}

        with patch(
            "inferia.services.api_gateway.gateway.router.rate_limiter"
        ) as mock_rl:
            mock_rl.check_rate_limit = AsyncMock()
            mock_db = AsyncMock()

            with pytest.raises(HTTPException) as exc:
                await list_models(mock_request, db=mock_db)
            assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_api_key_returns_401(self):
        """Models list with invalid key returns 401."""
        from inferia.services.api_gateway.gateway.router import list_models
        from fastapi import HTTPException

        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer sk-bad-key"}

        with patch(
            "inferia.services.api_gateway.gateway.router.rate_limiter"
        ) as mock_rl, patch(
            "inferia.services.api_gateway.gateway.router.policy_engine"
        ) as mock_pe:
            mock_rl.check_rate_limit = AsyncMock()
            mock_pe.verify_api_key = AsyncMock(return_value=None)
            mock_db = AsyncMock()

            with pytest.raises(HTTPException) as exc:
                await list_models(mock_request, db=mock_db)
            assert exc.value.status_code == 401
