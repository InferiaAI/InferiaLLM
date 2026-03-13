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
                "guardrail": {"enabled": False},
                "rag": {"enabled": False},
                "prompt_template": {"enabled": False},
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
    async def test_guardrail_config_forwarded(self):
        """Guardrail configuration is included in resolved context."""
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
                "guardrail": {"enabled": True, "toxicity_threshold": 0.8},
                "rag": {"enabled": False},
                "prompt_template": None,
                "rate_limit": None,
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
            assert response.guardrail_config["enabled"] is True
            assert response.guardrail_config["toxicity_threshold"] == 0.8


class TestScanContent:
    """scan_content guardrail endpoint."""

    @pytest.mark.asyncio
    async def test_scan_connection_error_raises_500(self):
        """Guardrail service unavailable returns 500."""
        from inferia.services.api_gateway.gateway.router import scan_content
        from inferia.common.schemas.guardrail import GuardrailScanRequest, ScanType
        from fastapi import HTTPException

        req_body = GuardrailScanRequest(text="test input", scan_type=ScanType.INPUT)
        mock_request = MagicMock()
        mock_request.state = MagicMock(spec=[])
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with patch(
            "inferia.services.api_gateway.gateway.router.rate_limiter"
        ) as mock_rl, patch(
            "inferia.services.api_gateway.gateway.router.gateway_http_client"
        ) as mock_hc:
            mock_rl.check_rate_limit = AsyncMock()
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
            mock_hc.get_service_client.return_value = mock_client
            mock_hc.get_internal_headers.return_value = {}

            with pytest.raises(HTTPException) as exc:
                await scan_content(req_body, mock_request)
            assert exc.value.status_code == 500


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
