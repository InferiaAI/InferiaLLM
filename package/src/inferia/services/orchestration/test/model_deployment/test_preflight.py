"""Tests for deployment preflight model accessibility check."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from inferia.services.orchestration.services.model_deployment.preflight import (
    check_model_accessibility,
    check_model_format,
    PreflightResult,
    FormatCheckResult,
)


def _mock_client(status_code=200, side_effect=None):
    """Helper to create a mocked httpx.AsyncClient."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code

    client_instance = AsyncMock()
    if side_effect:
        client_instance.head = AsyncMock(side_effect=side_effect)
    else:
        client_instance.head = AsyncMock(return_value=mock_resp)
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    return client_instance


class TestCheckModelAccessibility:

    @pytest.mark.asyncio
    async def test_public_model_returns_accessible(self):
        with patch("inferia.services.orchestration.services.model_deployment.preflight.httpx.AsyncClient", return_value=_mock_client(200)):
            result = await check_model_accessibility("meta-llama/Llama-3.1-8B")
            assert result.accessible is True
            assert result.needs_token is False
            assert result.error is None

    @pytest.mark.asyncio
    async def test_gated_model_without_token_needs_token(self):
        with patch("inferia.services.orchestration.services.model_deployment.preflight.httpx.AsyncClient", return_value=_mock_client(401)):
            result = await check_model_accessibility("meta-llama/Llama-3.1-8B")
            assert result.accessible is False
            assert result.needs_token is True

    @pytest.mark.asyncio
    async def test_gated_model_with_valid_token_accessible(self):
        with patch("inferia.services.orchestration.services.model_deployment.preflight.httpx.AsyncClient", return_value=_mock_client(200)):
            result = await check_model_accessibility("meta-llama/Llama-3.1-8B", hf_token="hf_valid")
            assert result.accessible is True
            assert result.needs_token is False

    @pytest.mark.asyncio
    async def test_gated_model_with_bad_token_no_needs_token(self):
        with patch("inferia.services.orchestration.services.model_deployment.preflight.httpx.AsyncClient", return_value=_mock_client(403)):
            result = await check_model_accessibility("meta-llama/Llama-3.1-8B", hf_token="hf_bad")
            assert result.accessible is False
            assert result.needs_token is False
            assert "token provided but" in result.error

    @pytest.mark.asyncio
    async def test_nonexistent_model_not_found(self):
        with patch("inferia.services.orchestration.services.model_deployment.preflight.httpx.AsyncClient", return_value=_mock_client(404)):
            result = await check_model_accessibility("nonexistent/model-xyz")
            assert result.accessible is False
            assert result.needs_token is False
            assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_connection_error_fails_open(self):
        with patch("inferia.services.orchestration.services.model_deployment.preflight.httpx.AsyncClient", return_value=_mock_client(side_effect=Exception("Connection refused"))):
            result = await check_model_accessibility("some/model")
            assert result.accessible is True
            assert result.skipped is True


def _mock_get_client(status_code=200, json_data=None, side_effect=None):
    """Helper for GET-based mocks (format check uses GET)."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json = MagicMock(return_value=json_data or {})

    client_instance = AsyncMock()
    if side_effect:
        client_instance.get = AsyncMock(side_effect=side_effect)
    else:
        client_instance.get = AsyncMock(return_value=mock_resp)
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    return client_instance


class TestCheckModelFormat:

    @pytest.mark.asyncio
    async def test_vllm_with_config_json_is_compatible(self):
        data = {"siblings": [{"rfilename": "config.json"}, {"rfilename": "model.safetensors"}]}
        with patch("inferia.services.orchestration.services.model_deployment.preflight.httpx.AsyncClient", return_value=_mock_get_client(200, data)):
            result = await check_model_format("org/model", "vllm")
            assert result.compatible is True
            assert result.has_config_json is True

    @pytest.mark.asyncio
    async def test_vllm_gguf_only_is_incompatible(self):
        data = {"siblings": [{"rfilename": "model-q4.gguf"}, {"rfilename": "README.md"}]}
        with patch("inferia.services.orchestration.services.model_deployment.preflight.httpx.AsyncClient", return_value=_mock_get_client(200, data)):
            result = await check_model_format("org/model", "vllm")
            assert result.compatible is False
            assert result.has_gguf is True
            assert "GGUF" in result.error
            assert "Ollama" in result.error

    @pytest.mark.asyncio
    async def test_vllm_no_config_no_gguf_is_incompatible(self):
        data = {"siblings": [{"rfilename": "README.md"}]}
        with patch("inferia.services.orchestration.services.model_deployment.preflight.httpx.AsyncClient", return_value=_mock_get_client(200, data)):
            result = await check_model_format("org/model", "vllm")
            assert result.compatible is False
            assert "config.json" in result.error

    @pytest.mark.asyncio
    async def test_ollama_skips_format_check(self):
        result = await check_model_format("org/model", "ollama")
        assert result.compatible is True
        assert result.skipped is True

    @pytest.mark.asyncio
    async def test_connection_error_fails_open(self):
        with patch("inferia.services.orchestration.services.model_deployment.preflight.httpx.AsyncClient", return_value=_mock_get_client(side_effect=Exception("timeout"))):
            result = await check_model_format("org/model", "vllm")
            assert result.compatible is True
            assert result.skipped is True
