"""Tests for deferred LLMGuard model loading (#78)."""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from inferia.services.guardrail.providers.llm_guard_provider import LLMGuardProvider
from inferia.services.guardrail.engine import GuardrailEngine


class TestLLMGuardProviderDeferredInit:
    def test_init_does_not_load_models(self):
        """__init__ should NOT call scanner initialization."""
        with patch.object(
            LLMGuardProvider, "_init_input_scanners"
        ) as mock_input, patch.object(
            LLMGuardProvider, "_init_output_scanners"
        ) as mock_output:
            provider = LLMGuardProvider()

        mock_input.assert_not_called()
        mock_output.assert_not_called()

    def test_not_ready_after_init(self):
        """Provider should not be ready until initialize() is called."""
        provider = LLMGuardProvider()
        assert provider.ready is False

    def test_scanners_empty_after_init(self):
        """Scanners should be empty lists after __init__."""
        provider = LLMGuardProvider()
        assert provider.input_scanners == []
        assert provider.output_scanners == []

    @pytest.mark.asyncio
    async def test_initialize_loads_models_in_thread(self):
        """initialize() should load models via asyncio.to_thread."""
        provider = LLMGuardProvider()

        fake_input = [MagicMock()]
        fake_output = [MagicMock()]

        with patch.object(
            provider, "_init_input_scanners", return_value=fake_input
        ), patch.object(
            provider, "_init_output_scanners", return_value=fake_output
        ), patch(
            "inferia.services.guardrail.providers.llm_guard_provider.asyncio.to_thread",
            new_callable=AsyncMock,
        ) as mock_to_thread:
            mock_to_thread.side_effect = [fake_input, fake_output]
            await provider.initialize()

        assert mock_to_thread.await_count == 2
        assert provider.input_scanners == fake_input
        assert provider.output_scanners == fake_output
        assert provider.ready is True

    @pytest.mark.asyncio
    async def test_initialize_sets_ready_true(self):
        """After successful initialize(), ready should be True."""
        provider = LLMGuardProvider()

        with patch.object(provider, "_init_input_scanners", return_value=[]), \
             patch.object(provider, "_init_output_scanners", return_value=[]), \
             patch(
                 "inferia.services.guardrail.providers.llm_guard_provider.asyncio.to_thread",
                 new_callable=AsyncMock,
                 side_effect=[[], []],
             ):
            await provider.initialize()

        assert provider.ready is True

    def test_name_property(self):
        provider = LLMGuardProvider()
        assert provider.name == "llm-guard"


class TestGuardrailEngineAsyncInit:
    def test_engine_init_creates_providers_without_model_loading(self):
        """GuardrailEngine.__init__ should create providers but not load models."""
        with patch(
            "inferia.services.guardrail.engine.LLMGuardProvider"
        ) as MockLLM, patch(
            "inferia.services.guardrail.engine.LlamaGuardProvider"
        ) as MockLlama, patch(
            "inferia.services.guardrail.engine.LakeraProvider"
        ) as MockLakera:
            mock_llm = MagicMock()
            mock_llm.name = "llm-guard"
            MockLLM.return_value = mock_llm

            mock_llama = MagicMock()
            mock_llama.name = "llama-guard"
            MockLlama.return_value = mock_llama

            mock_lakera = MagicMock()
            mock_lakera.name = "lakera-guard"
            MockLakera.return_value = mock_lakera

            engine = GuardrailEngine()

        assert "llm-guard" in engine.providers
        assert "llama-guard" in engine.providers
        assert "lakera-guard" in engine.providers

    @pytest.mark.asyncio
    async def test_engine_initialize_calls_provider_initialize(self):
        """engine.initialize() should call initialize() on providers that have it."""
        with patch(
            "inferia.services.guardrail.engine.LLMGuardProvider"
        ) as MockLLM, patch(
            "inferia.services.guardrail.engine.LlamaGuardProvider"
        ) as MockLlama, patch(
            "inferia.services.guardrail.engine.LakeraProvider"
        ) as MockLakera:
            mock_llm = MagicMock()
            mock_llm.name = "llm-guard"
            mock_llm.initialize = AsyncMock()
            MockLLM.return_value = mock_llm

            mock_llama = MagicMock()
            mock_llama.name = "llama-guard"
            mock_llama.initialize = AsyncMock()
            MockLlama.return_value = mock_llama

            mock_lakera = MagicMock()
            mock_lakera.name = "lakera-guard"
            # Lakera has no initialize method
            del mock_lakera.initialize
            MockLakera.return_value = mock_lakera

            engine = GuardrailEngine()
            await engine.initialize()

        mock_llm.initialize.assert_awaited_once()
        mock_llama.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_engine_initialize_handles_provider_failure(self):
        """If a provider's initialize() fails, other providers should still init."""
        with patch(
            "inferia.services.guardrail.engine.LLMGuardProvider"
        ) as MockLLM, patch(
            "inferia.services.guardrail.engine.LlamaGuardProvider"
        ) as MockLlama, patch(
            "inferia.services.guardrail.engine.LakeraProvider"
        ) as MockLakera:
            mock_llm = MagicMock()
            mock_llm.name = "llm-guard"
            mock_llm.initialize = AsyncMock(side_effect=RuntimeError("model download failed"))
            MockLLM.return_value = mock_llm

            mock_llama = MagicMock()
            mock_llama.name = "llama-guard"
            mock_llama.initialize = AsyncMock()
            MockLlama.return_value = mock_llama

            mock_lakera = MagicMock()
            mock_lakera.name = "lakera-guard"
            del mock_lakera.initialize
            MockLakera.return_value = mock_lakera

            engine = GuardrailEngine()
            # Should NOT raise
            await engine.initialize()

        # LLama should still have been initialized
        mock_llama.initialize.assert_awaited_once()
