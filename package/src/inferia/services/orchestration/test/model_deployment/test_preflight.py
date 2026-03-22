"""Tests for deployment preflight model accessibility check."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from inferia.services.orchestration.services.model_deployment.preflight import (
    check_model_accessibility,
    check_model_format,
    check_ollama_model_exists,
    check_vram_fit,
    check_pipeline_compatibility,
    check_docker_image_exists,
    check_duplicate_deployment,
    check_context_length,
    ENGINES_WITH_OWN_REGISTRY,
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


class TestCheckVRAMFit:

    def test_small_model_fits(self):
        """8B model (~20GB) fits in 1x 24GB GPU."""
        hf_info = {"safetensors": {"parameters": {"BF16": 8_000_000_000}}}
        result = check_vram_fit(hf_info, gpu_per_replica=1, gpu_vram_gb=24)
        assert result.ok is True
        assert result.estimated_vram_gb > 0

    def test_large_model_does_not_fit(self):
        """70B model (~175GB) does NOT fit in 1x 24GB GPU."""
        hf_info = {"safetensors": {"parameters": {"BF16": 70_000_000_000}}}
        result = check_vram_fit(hf_info, gpu_per_replica=1, gpu_vram_gb=24)
        assert result.ok is False
        assert "requires" in result.error

    def test_large_model_fits_with_multiple_gpus(self):
        """70B model fits in 8x 24GB = 192GB."""
        hf_info = {"safetensors": {"parameters": {"BF16": 70_000_000_000}}}
        result = check_vram_fit(hf_info, gpu_per_replica=8, gpu_vram_gb=24)
        assert result.ok is True

    def test_no_param_info_skips(self):
        """Missing safetensors info should skip, not fail."""
        hf_info = {"safetensors": {"parameters": {}}}
        result = check_vram_fit(hf_info, gpu_per_replica=1)
        assert result.ok is True
        assert result.skipped is True

    def test_none_hf_info_skips(self):
        result = check_vram_fit(None)
        assert result.ok is True
        assert result.skipped is True


class TestCheckPipelineCompatibility:

    def test_text_gen_model_with_vllm_compatible(self):
        hf_info = {"pipeline_tag": "text-generation"}
        result = check_pipeline_compatibility(hf_info, "vllm")
        assert result.compatible is True

    def test_embedding_model_with_vllm_incompatible(self):
        hf_info = {"pipeline_tag": "feature-extraction"}
        result = check_pipeline_compatibility(hf_info, "vllm")
        assert result.compatible is False
        assert "embeddings" in result.error.lower()

    def test_embedding_model_with_tei_compatible(self):
        hf_info = {"pipeline_tag": "feature-extraction"}
        result = check_pipeline_compatibility(hf_info, "tei")
        assert result.compatible is True

    def test_text_gen_model_with_tei_incompatible(self):
        hf_info = {"pipeline_tag": "text-generation"}
        result = check_pipeline_compatibility(hf_info, "tei")
        assert result.compatible is False

    def test_no_pipeline_tag_skips(self):
        hf_info = {"pipeline_tag": None}
        result = check_pipeline_compatibility(hf_info, "vllm")
        assert result.compatible is True
        assert result.skipped is True

    def test_unknown_engine_skips(self):
        hf_info = {"pipeline_tag": "text-generation"}
        result = check_pipeline_compatibility(hf_info, "some-custom-engine")
        assert result.compatible is True
        assert result.skipped is True


class TestCheckContextLength:

    def test_within_limits_passes(self):
        hf_info = {"config": {"max_position_embeddings": 8192}}
        result = check_context_length(hf_info, max_model_len=4096)
        assert result.ok is True

    def test_exceeds_limit_fails(self):
        hf_info = {"config": {"max_position_embeddings": 4096}}
        result = check_context_length(hf_info, max_model_len=8192)
        assert result.ok is False
        assert "exceeds" in result.error

    def test_no_config_skips(self):
        hf_info = {"config": {}}
        result = check_context_length(hf_info, max_model_len=4096)
        assert result.ok is True
        assert result.skipped is True

    def test_no_max_model_len_skips(self):
        hf_info = {"config": {"max_position_embeddings": 8192}}
        result = check_context_length(hf_info, max_model_len=None)
        assert result.ok is True
        assert result.skipped is True


class TestCheckDuplicateDeployment:

    @pytest.mark.asyncio
    async def test_no_duplicate_passes(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))
        result = await check_duplicate_deployment("org/model", "pool-1", mock_pool)
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_existing_deployment_fails(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"id": "dep-123"})
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=False)))
        result = await check_duplicate_deployment("org/model", "pool-1", mock_pool)
        assert result.ok is False
        assert "already has" in result.error

    @pytest.mark.asyncio
    async def test_external_pool_id_skips(self):
        result = await check_duplicate_deployment("org/model", "00000000-0000-0000-0000-000000000000", None)
        assert result.ok is True
        assert result.skipped is True


class TestEnginesWithOwnRegistry:
    """Engines like ollama use model:tag format, not HF org/model."""

    def test_ollama_is_in_own_registry(self):
        assert "ollama" in ENGINES_WITH_OWN_REGISTRY

    def test_localai_is_in_own_registry(self):
        assert "localai" in ENGINES_WITH_OWN_REGISTRY

    def test_vllm_is_not_in_own_registry(self):
        assert "vllm" not in ENGINES_WITH_OWN_REGISTRY

    @pytest.mark.asyncio
    async def test_ollama_model_id_not_sent_to_hf(self):
        """Ollama model IDs like 'llama3:8b' should NOT be checked against HF API."""
        result = await check_model_format("llama3:8b", "ollama")
        assert result.compatible is True
        assert result.skipped is True


def _mock_head_client(status_code=200, side_effect=None):
    """Helper for HEAD-based mocks with follow_redirects."""
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


class TestCheckOllamaModelExists:

    @pytest.mark.asyncio
    async def test_existing_model_returns_accessible(self):
        with patch("inferia.services.orchestration.services.model_deployment.preflight.httpx.AsyncClient", return_value=_mock_head_client(200)):
            result = await check_ollama_model_exists("llama3:8b")
            assert result.accessible is True

    @pytest.mark.asyncio
    async def test_nonexistent_model_returns_not_found(self):
        with patch("inferia.services.orchestration.services.model_deployment.preflight.httpx.AsyncClient", return_value=_mock_head_client(404)):
            result = await check_ollama_model_exists("nonexistent-model-xyz")
            assert result.accessible is False
            assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_model_without_tag(self):
        with patch("inferia.services.orchestration.services.model_deployment.preflight.httpx.AsyncClient", return_value=_mock_head_client(200)):
            result = await check_ollama_model_exists("llama3")
            assert result.accessible is True

    @pytest.mark.asyncio
    async def test_namespaced_model(self):
        """Namespaced models like 'mannix/model' use a different URL path."""
        with patch("inferia.services.orchestration.services.model_deployment.preflight.httpx.AsyncClient", return_value=_mock_head_client(200)) as mock_cls:
            result = await check_ollama_model_exists("mannix/llama3.1-8b")
            assert result.accessible is True
            # Verify URL doesn't use /library/ for namespaced models
            call_args = mock_cls.return_value.head.call_args[0][0]
            assert "/library/" not in call_args
            assert "mannix/llama3.1-8b" in call_args

    @pytest.mark.asyncio
    async def test_connection_error_fails_open(self):
        with patch("inferia.services.orchestration.services.model_deployment.preflight.httpx.AsyncClient", return_value=_mock_head_client(side_effect=Exception("timeout"))):
            result = await check_ollama_model_exists("llama3")
            assert result.accessible is True
            assert result.skipped is True

    @pytest.mark.asyncio
    async def test_empty_model_id_skips(self):
        result = await check_ollama_model_exists("")
        assert result.accessible is True
        assert result.skipped is True
