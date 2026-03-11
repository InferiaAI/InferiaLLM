"""Tests for guardrail engine orchestration — complex logic layer."""

import pytest
from unittest.mock import AsyncMock, patch

from inferia.services.guardrail.models import GuardrailResult, Violation, ViolationType


class TestGuardrailEngineOrchestration:
    """Verify engine orchestration logic."""

    @pytest.mark.asyncio
    async def test_provider_disabled_via_config_not_called(self):
        """When guardrails disabled in config, provider is not called."""
        mock_provider = AsyncMock()
        mock_provider.name = "mock-guard"

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

            # Disable via config override
            result = await engine.scan_input("test", config={"enabled": False})
            assert result.is_valid is True
            mock_provider.scan_input.assert_not_called()

    @pytest.mark.asyncio
    async def test_custom_keywords_passed_to_provider(self):
        """Custom keywords are forwarded in metadata."""
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

            await engine.scan_input("test", custom_keywords=["banned_word"])
            call_args = mock_provider.scan_input.call_args
            metadata = call_args[0][3]  # 4th positional arg is metadata
            assert metadata["custom_keywords"] == ["banned_word"]

    @pytest.mark.asyncio
    async def test_proceed_on_violation_overrides_result(self):
        """When proceed_on_violation=True, violations don't block."""
        mock_provider = AsyncMock()
        mock_provider.name = "mock-guard"
        mock_provider.scan_input.return_value = GuardrailResult(
            is_valid=False,
            sanitized_text="bad",
            violations=[
                Violation(
                    scanner="test",
                    violation_type=ViolationType.TOXICITY,
                    score=0.9,
                    details="toxic content",
                )
            ],
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

            result = await engine.scan_input(
                "test", config={"proceed_on_violation": True}
            )
            # Despite violation, is_valid is overridden to True
            assert result.is_valid is True
            assert "proceed_on_violation_warning" in result.actions_taken
