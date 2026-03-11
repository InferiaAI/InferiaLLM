"""
Tests that guardrail scanners fail closed on errors.

Verifies that:
1. LlamaGuardProvider returns is_valid=False when Groq API call fails
2. PIIService returns violations when scanner init or scan fails
3. Error details are included in violations for debugging
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from inferia.common.schemas.guardrail import ViolationType


class TestLlamaGuardFailsClosed:
    """Verify LlamaGuardProvider fails closed on API errors."""

    @pytest.mark.asyncio
    async def test_scan_input_api_error_returns_invalid(self):
        """scan_input must return is_valid=False when Groq API throws."""
        from inferia.services.guardrail.providers.llama_guard_provider import (
            LlamaGuardProvider,
        )

        provider = LlamaGuardProvider.__new__(LlamaGuardProvider)
        provider.settings = MagicMock()
        provider.settings.llama_guard_model_id = "llama-guard-3-8b"

        # Mock a Groq client that raises on API call
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception(
            "Groq API rate limit exceeded"
        )
        provider.groq_client = mock_client

        result = await provider.scan_input(
            text="test input",
            user_id="user-1",
            config={"input_scanners": []},
        )

        assert result.is_valid is False
        assert len(result.violations) == 1
        assert result.violations[0].scanner == "LlamaGuard"
        assert result.violations[0].violation_type == ViolationType.EXTERNAL_SERVICE_ERROR
        assert "failed" in result.violations[0].details.lower()

    @pytest.mark.asyncio
    async def test_scan_output_api_error_returns_invalid(self):
        """scan_output must return is_valid=False when Groq API throws."""
        from inferia.services.guardrail.providers.llama_guard_provider import (
            LlamaGuardProvider,
        )

        provider = LlamaGuardProvider.__new__(LlamaGuardProvider)
        provider.settings = MagicMock()
        provider.settings.llama_guard_model_id = "llama-guard-3-8b"

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = ConnectionError(
            "Connection refused"
        )
        provider.groq_client = mock_client

        result = await provider.scan_output(
            text="user prompt",
            output="model response with sensitive data",
            user_id="user-1",
            config={},
        )

        assert result.is_valid is False
        assert len(result.violations) == 1
        assert result.violations[0].scanner == "LlamaGuard"
        assert result.violations[0].violation_type == ViolationType.EXTERNAL_SERVICE_ERROR

    @pytest.mark.asyncio
    async def test_scan_input_no_client_returns_invalid(self):
        """scan_input with no Groq client must return is_valid=False."""
        from inferia.services.guardrail.providers.llama_guard_provider import (
            LlamaGuardProvider,
        )

        provider = LlamaGuardProvider.__new__(LlamaGuardProvider)
        provider.settings = MagicMock()
        provider.groq_client = None  # No client initialized

        result = await provider.scan_input(
            text="test", user_id="user-1", config={}
        )

        assert result.is_valid is False
        assert len(result.violations) == 1
        assert "Missing Groq API Key" in result.violations[0].details

    @pytest.mark.asyncio
    async def test_scan_input_records_scan_time(self):
        """Failed scans must still record scan_time_ms."""
        from inferia.services.guardrail.providers.llama_guard_provider import (
            LlamaGuardProvider,
        )

        provider = LlamaGuardProvider.__new__(LlamaGuardProvider)
        provider.settings = MagicMock()
        provider.settings.llama_guard_model_id = "llama-guard-3-8b"

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("timeout")
        provider.groq_client = mock_client

        result = await provider.scan_input(
            text="test", user_id="user-1", config={}
        )

        assert result.scan_time_ms >= 0


class TestPIIServiceFailsClosed:
    """Verify PIIService fails closed on scanner errors."""

    @pytest.mark.asyncio
    async def test_scanner_init_failure_returns_violations(self):
        """When scanner fails to initialize, must return violations (not empty list)."""
        from inferia.services.guardrail.pii_service import PIIService

        service = PIIService.__new__(PIIService)
        service.settings = MagicMock()
        service.vault = MagicMock()
        service._anonymize_cache = {}
        service._lock = asyncio.Lock()

        # _get_anonymize_scanner returns None when init fails
        with patch.object(service, "_get_anonymize_scanner", return_value=None):
            text, violations = await service.anonymize("John Doe lives at 123 Main St")

        # Must return violations, not empty list
        assert len(violations) == 1
        assert violations[0].scanner == "Anonymize"
        assert violations[0].violation_type == ViolationType.EXTERNAL_SERVICE_ERROR
        assert "failed to initialize" in violations[0].details.lower()
        # Original text returned (but with violation flag for caller to handle)
        assert text == "John Doe lives at 123 Main St"

    @pytest.mark.asyncio
    async def test_scan_exception_returns_violations(self):
        """When scan_prompt throws, must return violations (not empty list)."""
        from inferia.services.guardrail.pii_service import PIIService

        service = PIIService.__new__(PIIService)
        service.settings = MagicMock()
        service.vault = MagicMock()
        service._anonymize_cache = {}
        service._lock = asyncio.Lock()

        mock_scanner = MagicMock()

        with patch.object(
            service, "_get_anonymize_scanner", return_value=mock_scanner
        ), patch(
            "inferia.services.guardrail.pii_service.scan_prompt",
            side_effect=RuntimeError("NER model failed to load"),
        ):
            text, violations = await service.anonymize("sensitive PII data")

        assert len(violations) == 1
        assert violations[0].scanner == "Anonymize"
        assert violations[0].violation_type == ViolationType.EXTERNAL_SERVICE_ERROR
        assert "failed" in violations[0].details.lower()

    @pytest.mark.asyncio
    async def test_successful_scan_still_works(self):
        """Normal PII detection must still work correctly."""
        from inferia.services.guardrail.pii_service import PIIService

        service = PIIService.__new__(PIIService)
        service.settings = MagicMock()
        service.vault = MagicMock()
        service._anonymize_cache = {}
        service._lock = asyncio.Lock()

        mock_scanner = MagicMock()

        with patch.object(
            service, "_get_anonymize_scanner", return_value=mock_scanner
        ), patch(
            "inferia.services.guardrail.pii_service.scan_prompt",
            return_value=("[REDACTED] lives at [REDACTED]", {}, {"Anonymize": 0.8}),
        ):
            text, violations = await service.anonymize("John Doe lives at 123 Main St")

        assert text == "[REDACTED] lives at [REDACTED]"
        assert len(violations) == 1
        assert violations[0].violation_type == ViolationType.PII
