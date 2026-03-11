"""Tests for API gateway error handling."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException


class TestGatewayRouterErrors:
    """Verify gateway router error paths."""

    @pytest.mark.asyncio
    async def test_guardrail_scan_service_unavailable_returns_500(self):
        """Guardrail service connection failure returns 500."""
        from inferia.services.api_gateway.gateway.router import scan_content
        from inferia.common.schemas.guardrail import GuardrailScanRequest, ScanType

        request_body = GuardrailScanRequest(
            text="test input", scan_type=ScanType.INPUT
        )
        mock_request = MagicMock()
        mock_request.state = MagicMock(spec=[])  # No 'user' attribute
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"

        with patch(
            "inferia.services.api_gateway.gateway.router.rate_limiter"
        ) as mock_rl, patch(
            "inferia.services.api_gateway.gateway.router.gateway_http_client"
        ) as mock_hc:
            mock_rl.check_rate_limit = AsyncMock()
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=Exception("Connection refused")
            )
            mock_hc.get_service_client.return_value = mock_client
            mock_hc.get_internal_headers.return_value = {}

            with pytest.raises(HTTPException) as exc:
                await scan_content(request_body, mock_request)
            assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_resolve_context_invalid_key_returns_error(self):
        """Context resolution with invalid key returns valid=false."""
        from inferia.services.api_gateway.gateway.router import (
            resolve_inference_context,
            ResolveContextRequest,
        )

        request = ResolveContextRequest(api_key="bad-key", model="test-model")

        with patch(
            "inferia.services.api_gateway.gateway.router.policy_engine"
        ) as mock_pe:
            mock_pe.resolve_context = AsyncMock(
                return_value={"valid": False, "error": "Invalid API Key"}
            )
            mock_db = AsyncMock()
            response = await resolve_inference_context(request, mock_db)
            assert response.valid is False
            assert "Invalid" in response.error

    @pytest.mark.asyncio
    async def test_check_quota_exceeded_returns_429(self):
        """Quota check raises 429 when exceeded."""
        from inferia.services.api_gateway.gateway.router import (
            check_user_quota,
            QuotaCheckRequest,
        )

        request = QuotaCheckRequest(user_id="user-1", model="gpt-4")

        with patch(
            "inferia.services.api_gateway.gateway.router.policy_engine"
        ) as mock_pe:
            mock_pe.check_quota = AsyncMock(
                side_effect=HTTPException(status_code=429, detail="Quota exceeded")
            )
            mock_db = AsyncMock()
            with pytest.raises(HTTPException) as exc:
                await check_user_quota(request, mock_db)
            assert exc.value.status_code == 429

    @pytest.mark.asyncio
    async def test_track_usage_succeeds(self):
        """Usage tracking returns ok status."""
        from inferia.services.api_gateway.gateway.router import (
            track_user_usage,
            UsageTrackRequest,
        )

        request = UsageTrackRequest(
            user_id="user-1",
            model="gpt-4",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )

        with patch(
            "inferia.services.api_gateway.gateway.router.policy_engine"
        ) as mock_pe:
            mock_pe.increment_redis_only = AsyncMock()
            mock_db = AsyncMock()
            mock_bg = MagicMock()
            response = await track_user_usage(request, mock_bg, mock_db)
            assert response["status"] == "ok"
            mock_pe.increment_redis_only.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_log_returns_log_id(self):
        """Log creation returns a log ID."""
        from inferia.services.api_gateway.gateway.router import create_inference_log
        from inferia.services.api_gateway.models import InferenceLogCreate

        log_data = InferenceLogCreate(
            deployment_id="dep-1",
            user_id="user-1",
            model="gpt-4",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        response = await create_inference_log(log_data, mock_bg, mock_db)
        assert "log_id" in response
        assert response["status"] == "ok"

    @pytest.mark.asyncio
    async def test_credentials_endpoint_requires_internal_key(self):
        """Credentials endpoint rejects requests without valid internal key."""
        from inferia.services.api_gateway.gateway.router import (
            get_provider_credentials_internal,
        )

        mock_request = MagicMock()
        mock_request.headers = {}

        with pytest.raises(HTTPException) as exc:
            await get_provider_credentials_internal(mock_request)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_models_endpoint_requires_auth(self):
        """Models list endpoint rejects requests without Bearer token."""
        from inferia.services.api_gateway.gateway.router import list_models

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
