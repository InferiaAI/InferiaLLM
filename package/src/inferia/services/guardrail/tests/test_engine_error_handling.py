"""Tests for guardrail engine error handling."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from inferia.services.guardrail.models import GuardrailResult, Violation, ViolationType


class TestGuardrailEngineErrors:
    """Verify engine handles provider failures correctly."""

    @pytest.mark.asyncio
    async def test_provider_init_failure_engine_still_starts(self):
        """Engine starts even if a provider fails to init."""
        with patch(
            "inferia.services.guardrail.engine.LLMGuardProvider",
            side_effect=Exception("init failed"),
        ), patch(
            "inferia.services.guardrail.engine.LlamaGuardProvider",
            side_effect=Exception("init failed"),
        ), patch(
            "inferia.services.guardrail.engine.LakeraProvider",
            side_effect=Exception("init failed"),
        ):
            from inferia.services.guardrail.engine import GuardrailEngine
            engine = GuardrailEngine()
            # Engine created but no providers
            assert len(engine.providers) == 0

    @pytest.mark.asyncio
    async def test_no_providers_returns_is_valid_true(self):
        """When no providers available, returns is_valid=True (fail-open)."""
        with patch(
            "inferia.services.guardrail.engine.LLMGuardProvider",
            side_effect=Exception("init failed"),
        ), patch(
            "inferia.services.guardrail.engine.LlamaGuardProvider",
            side_effect=Exception("init failed"),
        ), patch(
            "inferia.services.guardrail.engine.LakeraProvider",
            side_effect=Exception("init failed"),
        ):
            from inferia.services.guardrail.engine import GuardrailEngine
            engine = GuardrailEngine()
            result = await engine.scan_input("test prompt")
            assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_provider_throws_during_scan_returns_fail_closed(self):
        """When a provider exists but throws during scan, returns is_valid=False."""
        mock_provider = AsyncMock()
        mock_provider.name = "mock-guard"
        mock_provider.scan_input.side_effect = RuntimeError("scan crashed")

        with patch(
            "inferia.services.guardrail.engine.LLMGuardProvider",
            side_effect=Exception("skip"),
        ), patch(
            "inferia.services.guardrail.engine.LlamaGuardProvider",
            side_effect=Exception("skip"),
        ), patch(
            "inferia.services.guardrail.engine.LakeraProvider",
            side_effect=Exception("skip"),
        ):
            from inferia.services.guardrail.engine import GuardrailEngine
            engine = GuardrailEngine()
            # Manually inject our mock provider
            engine.providers["mock-guard"] = mock_provider
            engine.settings.default_guardrail_engine = "mock-guard"

            result = await engine.scan_input("test prompt")
            assert result.is_valid is False
            assert len(result.violations) == 1
            assert result.violations[0].violation_type == ViolationType.EXTERNAL_SERVICE_ERROR

    @pytest.mark.asyncio
    async def test_one_provider_fails_others_succeed(self):
        """When multiple providers exist but only the selected one works."""
        good_provider = AsyncMock()
        good_provider.name = "good-guard"
        good_result = GuardrailResult(is_valid=True, sanitized_text="clean")
        good_provider.scan_input.return_value = good_result

        with patch(
            "inferia.services.guardrail.engine.LLMGuardProvider",
            side_effect=Exception("skip"),
        ), patch(
            "inferia.services.guardrail.engine.LlamaGuardProvider",
            side_effect=Exception("skip"),
        ), patch(
            "inferia.services.guardrail.engine.LakeraProvider",
            side_effect=Exception("skip"),
        ):
            from inferia.services.guardrail.engine import GuardrailEngine
            engine = GuardrailEngine()
            engine.providers["good-guard"] = good_provider
            engine.settings.default_guardrail_engine = "good-guard"

            result = await engine.scan_input("hello")
            assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_config_update_mid_scan_no_crash(self):
        """Changing settings during scan doesn't crash."""
        mock_provider = AsyncMock()
        mock_provider.name = "mock-guard"
        mock_provider.scan_input.return_value = GuardrailResult(
            is_valid=True, sanitized_text="ok"
        )

        with patch(
            "inferia.services.guardrail.engine.LLMGuardProvider",
            side_effect=Exception("skip"),
        ), patch(
            "inferia.services.guardrail.engine.LlamaGuardProvider",
            side_effect=Exception("skip"),
        ), patch(
            "inferia.services.guardrail.engine.LakeraProvider",
            side_effect=Exception("skip"),
        ):
            from inferia.services.guardrail.engine import GuardrailEngine
            engine = GuardrailEngine()
            engine.providers["mock-guard"] = mock_provider
            engine.settings.default_guardrail_engine = "mock-guard"

            # Simulate config change mid-flight
            engine.settings.enable_guardrails = True
            result = await engine.scan_input("test")
            assert result.is_valid is True
