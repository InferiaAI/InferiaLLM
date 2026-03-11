"""Tests for guardrail scan endpoint security."""

import pytest
from unittest.mock import AsyncMock, patch

from inferia.services.guardrail.models import GuardrailResult, ScanType


class TestScanEndpointSecurity:
    """Verify scan endpoint security."""

    @pytest.mark.asyncio
    async def test_scan_input_calls_engine_correctly(self):
        from inferia.services.guardrail.app import scan, ScanRequest

        request = ScanRequest(
            text="test input", scan_type=ScanType.INPUT, user_id="user-1"
        )

        mock_result = GuardrailResult(is_valid=True, sanitized_text="test input")
        with patch("inferia.services.guardrail.app.guardrail_engine") as mock_engine:
            mock_engine.scan_input = AsyncMock(return_value=mock_result)
            result = await scan(request)
            assert result.is_valid is True
            mock_engine.scan_input.assert_called_once()

    @pytest.mark.asyncio
    async def test_scan_output_routes_correctly(self):
        from inferia.services.guardrail.app import scan, ScanRequest

        request = ScanRequest(
            text="output text",
            scan_type=ScanType.OUTPUT,
            context="input prompt",
            user_id="user-1",
        )

        mock_result = GuardrailResult(is_valid=True, sanitized_text="output text")
        with patch("inferia.services.guardrail.app.guardrail_engine") as mock_engine:
            mock_engine.scan_output = AsyncMock(return_value=mock_result)
            result = await scan(request)
            assert result.is_valid is True
            mock_engine.scan_output.assert_called_once()

    @pytest.mark.asyncio
    async def test_scan_engine_error_returns_500(self):
        from inferia.services.guardrail.app import scan, ScanRequest
        from fastapi import HTTPException

        request = ScanRequest(text="test", user_id="user-1")

        with patch("inferia.services.guardrail.app.guardrail_engine") as mock_engine:
            mock_engine.scan_input = AsyncMock(
                side_effect=Exception("engine crash")
            )
            with pytest.raises(HTTPException) as exc:
                await scan(request)
            assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_scan_default_user_id_is_unknown(self):
        from inferia.services.guardrail.app import scan, ScanRequest

        request = ScanRequest(text="test")

        mock_result = GuardrailResult(is_valid=True, sanitized_text="test")
        with patch("inferia.services.guardrail.app.guardrail_engine") as mock_engine:
            mock_engine.scan_input = AsyncMock(return_value=mock_result)
            await scan(request)
            call_kwargs = mock_engine.scan_input.call_args.kwargs
            assert call_kwargs["user_id"] == "unknown"

    @pytest.mark.asyncio
    async def test_scan_passes_custom_keywords(self):
        from inferia.services.guardrail.app import scan, ScanRequest

        request = ScanRequest(
            text="test",
            user_id="user-1",
            custom_banned_keywords=["blocked_word"],
        )

        mock_result = GuardrailResult(is_valid=True, sanitized_text="test")
        with patch("inferia.services.guardrail.app.guardrail_engine") as mock_engine:
            mock_engine.scan_input = AsyncMock(return_value=mock_result)
            await scan(request)
            call_kwargs = mock_engine.scan_input.call_args.kwargs
            assert call_kwargs["custom_keywords"] == ["blocked_word"]
