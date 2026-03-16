"""Tests for proceed_on_violation — verify no prompt injection into sanitized text."""

import pytest
from unittest.mock import AsyncMock, patch

from inferia.services.guardrail.models import GuardrailResult, Violation, ViolationType


class TestProceedOnViolationNoInjection:
    """Verify proceed_on_violation does not inject [SYSTEM:] text into the prompt."""

    @pytest.mark.asyncio
    async def test_sanitized_text_has_no_system_tag(self):
        """When proceed_on_violation=True, sanitized_text must not contain [SYSTEM:."""
        mock_provider = AsyncMock()
        mock_provider.name = "mock-guard"
        mock_provider.scan_input.return_value = GuardrailResult(
            is_valid=False,
            sanitized_text="user input here",
            violations=[
                Violation(
                    scanner="test",
                    violation_type=ViolationType.TOXICITY,
                    score=0.95,
                    details="toxic content detected",
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
                "user input here", config={"proceed_on_violation": True}
            )

            assert "[SYSTEM:" not in (result.sanitized_text or "")

    @pytest.mark.asyncio
    async def test_is_valid_set_to_true(self):
        """When proceed_on_violation=True, result.is_valid must be True."""
        mock_provider = AsyncMock()
        mock_provider.name = "mock-guard"
        mock_provider.scan_input.return_value = GuardrailResult(
            is_valid=False,
            sanitized_text="test prompt",
            violations=[
                Violation(
                    scanner="test",
                    violation_type=ViolationType.PROMPT_INJECTION,
                    score=0.85,
                    details="injection attempt",
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
                "test prompt", config={"proceed_on_violation": True}
            )

            assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_proceed_on_violation_in_actions_taken(self):
        """When proceed_on_violation=True, 'proceed_on_violation' must be in actions_taken."""
        mock_provider = AsyncMock()
        mock_provider.name = "mock-guard"
        mock_provider.scan_input.return_value = GuardrailResult(
            is_valid=False,
            sanitized_text="test prompt",
            violations=[
                Violation(
                    scanner="test",
                    violation_type=ViolationType.TOXICITY,
                    score=0.9,
                    details="toxic",
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
                "test prompt", config={"proceed_on_violation": True}
            )

            assert "proceed_on_violation" in result.actions_taken

    @pytest.mark.asyncio
    async def test_violations_still_recorded(self):
        """When proceed_on_violation=True, violations must still be in result.violations."""
        mock_provider = AsyncMock()
        mock_provider.name = "mock-guard"
        mock_provider.scan_input.return_value = GuardrailResult(
            is_valid=False,
            sanitized_text="test prompt",
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
                "test prompt", config={"proceed_on_violation": True}
            )

            assert len(result.violations) == 1
            assert result.violations[0].violation_type == ViolationType.TOXICITY
            assert result.violations[0].score == 0.9
