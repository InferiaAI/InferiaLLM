"""Tests for media generation orchestrator handlers."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import BackgroundTasks, HTTPException


MOCK_CONTEXT = {
    "valid": True,
    "deployment": {
        "id": "test-deploy-id",
        "endpoint": "http://localhost:9000",
        "engine": "diffusers",
    },
    "user_id_context": "user-123",
    "rate_limit_config": None,
    "log_payloads": True,
}


@pytest.fixture
def background_tasks():
    return BackgroundTasks()


class TestHandleImageGeneration:
    @pytest.mark.asyncio
    async def test_happy_path(self, background_tasks):
        from inferia.services.inference.core.orchestrator import OrchestrationService

        image_response = {
            "created": 1234567890,
            "data": [{"url": "http://images/cat.png"}],
        }

        with patch.object(
            OrchestrationService, "_log_media_request", new_callable=AsyncMock
        ), patch(
            "inferia.services.inference.core.orchestrator.GatewayService"
        ) as mock_gw, patch(
            "inferia.services.inference.core.orchestrator.api_gateway_client"
        ) as mock_client:
            mock_gw.resolve_context = AsyncMock(return_value=MOCK_CONTEXT)
            mock_gw.call_upstream = AsyncMock(return_value=image_response)
            mock_client.check_quota = AsyncMock()

            result = await OrchestrationService.handle_image_generation(
                api_key="test-key",
                body={"model": "sdxl", "prompt": "a cat in space", "n": 1, "size": "1024x1024"},
                background_tasks=background_tasks,
            )

            assert result == image_response
            mock_gw.resolve_context.assert_called_once_with(
                "test-key", "sdxl", model_type="image_generation"
            )

    @pytest.mark.asyncio
    async def test_missing_prompt_raises_400(self, background_tasks):
        from inferia.services.inference.core.orchestrator import OrchestrationService

        with pytest.raises(HTTPException) as exc_info:
            await OrchestrationService.handle_image_generation(
                api_key="test-key",
                body={"model": "sdxl"},
                background_tasks=background_tasks,
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_model_raises_400(self, background_tasks):
        from inferia.services.inference.core.orchestrator import OrchestrationService

        with pytest.raises(HTTPException) as exc_info:
            await OrchestrationService.handle_image_generation(
                api_key="test-key",
                body={"prompt": "a cat"},
                background_tasks=background_tasks,
            )
        assert exc_info.value.status_code == 400


class TestHandleVideoGeneration:
    @pytest.mark.asyncio
    async def test_happy_path(self, background_tasks):
        from inferia.services.inference.core.orchestrator import OrchestrationService

        video_response = {
            "created": 1234567890,
            "data": [{"url": "http://videos/cat.mp4"}],
        }

        video_context = {
            **MOCK_CONTEXT,
            "deployment": {**MOCK_CONTEXT["deployment"], "engine": "diffusers-video"},
        }

        with patch.object(
            OrchestrationService, "_log_media_request", new_callable=AsyncMock
        ), patch(
            "inferia.services.inference.core.orchestrator.GatewayService"
        ) as mock_gw, patch(
            "inferia.services.inference.core.orchestrator.api_gateway_client"
        ) as mock_client:
            mock_gw.resolve_context = AsyncMock(return_value=video_context)
            mock_gw.call_upstream = AsyncMock(return_value=video_response)
            mock_client.check_quota = AsyncMock()

            result = await OrchestrationService.handle_video_generation(
                api_key="test-key",
                body={"model": "svd", "prompt": "a cat walking"},
                background_tasks=background_tasks,
            )

            assert result == video_response
            mock_gw.resolve_context.assert_called_once_with(
                "test-key", "svd", model_type="video_generation"
            )

    @pytest.mark.asyncio
    async def test_missing_prompt_raises_400(self, background_tasks):
        from inferia.services.inference.core.orchestrator import OrchestrationService

        with pytest.raises(HTTPException) as exc_info:
            await OrchestrationService.handle_video_generation(
                api_key="test-key",
                body={"model": "svd"},
                background_tasks=background_tasks,
            )
        assert exc_info.value.status_code == 400


class TestHandleAudioSpeech:
    @pytest.mark.asyncio
    async def test_happy_path_returns_streaming_response(self, background_tasks):
        from inferia.services.inference.core.orchestrator import OrchestrationService
        from fastapi.responses import StreamingResponse

        audio_bytes = b"\xff\xfb\x90\x00" * 100

        audio_context = {
            **MOCK_CONTEXT,
            "deployment": {**MOCK_CONTEXT["deployment"], "engine": "bark"},
        }

        with patch.object(
            OrchestrationService, "_log_media_request", new_callable=AsyncMock
        ), patch(
            "inferia.services.inference.core.orchestrator.GatewayService"
        ) as mock_gw, patch(
            "inferia.services.inference.core.orchestrator.api_gateway_client"
        ) as mock_client:
            mock_gw.resolve_context = AsyncMock(return_value=audio_context)
            mock_gw.call_upstream_raw = AsyncMock(return_value=audio_bytes)
            mock_client.check_quota = AsyncMock()

            result = await OrchestrationService.handle_audio_speech(
                api_key="test-key",
                body={"model": "bark", "input": "Hello world", "voice": "alloy"},
                background_tasks=background_tasks,
            )

            assert isinstance(result, StreamingResponse)
            assert result.media_type == "audio/mpeg"

    @pytest.mark.asyncio
    async def test_missing_input_raises_400(self, background_tasks):
        from inferia.services.inference.core.orchestrator import OrchestrationService

        with pytest.raises(HTTPException) as exc_info:
            await OrchestrationService.handle_audio_speech(
                api_key="test-key",
                body={"model": "bark"},
                background_tasks=background_tasks,
            )
        assert exc_info.value.status_code == 400


class TestHandleAudioTranscription:
    @pytest.mark.asyncio
    async def test_happy_path(self, background_tasks):
        from inferia.services.inference.core.orchestrator import OrchestrationService

        transcription_response = {"text": "Hello, this is a test."}

        audio_context = {
            **MOCK_CONTEXT,
            "deployment": {**MOCK_CONTEXT["deployment"], "engine": "whisper"},
        }

        with patch.object(
            OrchestrationService, "_log_media_request", new_callable=AsyncMock
        ), patch(
            "inferia.services.inference.core.orchestrator.GatewayService"
        ) as mock_gw, patch(
            "inferia.services.inference.core.orchestrator.api_gateway_client"
        ) as mock_client:
            mock_gw.resolve_context = AsyncMock(return_value=audio_context)
            mock_gw.call_upstream_multipart = AsyncMock(return_value=transcription_response)
            mock_client.check_quota = AsyncMock()

            mock_file = AsyncMock()
            mock_file.filename = "test.mp3"
            mock_file.content_type = "audio/mpeg"
            mock_file.read = AsyncMock(return_value=b"fake audio content")

            result = await OrchestrationService.handle_audio_transcription(
                api_key="test-key",
                form_data={"model": "whisper-large-v3", "file": mock_file},
                background_tasks=background_tasks,
            )

            assert result == transcription_response

    @pytest.mark.asyncio
    async def test_missing_file_raises_400(self, background_tasks):
        from inferia.services.inference.core.orchestrator import OrchestrationService

        with pytest.raises(HTTPException) as exc_info:
            await OrchestrationService.handle_audio_transcription(
                api_key="test-key",
                form_data={"model": "whisper"},
                background_tasks=background_tasks,
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_model_raises_400(self, background_tasks):
        from inferia.services.inference.core.orchestrator import OrchestrationService

        mock_file = AsyncMock()
        mock_file.filename = "test.mp3"

        with pytest.raises(HTTPException) as exc_info:
            await OrchestrationService.handle_audio_transcription(
                api_key="test-key",
                form_data={"file": mock_file},
                background_tasks=background_tasks,
            )
        assert exc_info.value.status_code == 400


class TestMultimodalDetection:
    """Test that handle_completion detects multimodal content."""

    @pytest.mark.asyncio
    async def test_image_url_content_resolves_as_multimodal(self, background_tasks):
        from inferia.services.inference.core.orchestrator import OrchestrationService
        import asyncio

        multimodal_context = {
            **MOCK_CONTEXT,
            "deployment": {**MOCK_CONTEXT["deployment"], "engine": "vllm"},
            "guardrail_config": {},
            "rag_config": {},
            "template_config": None,
            "org_id": "org-1",
        }

        body = {
            "model": "llava-7b",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is in this image?"},
                        {"type": "image_url", "image_url": {"url": "http://img.png"}},
                    ],
                }
            ],
        }

        with patch(
            "inferia.services.inference.core.orchestrator.GatewayService"
        ) as mock_gw, patch(
            "inferia.services.inference.core.orchestrator.api_gateway_client"
        ) as mock_client, patch(
            "inferia.services.inference.core.orchestrator.get_adapter"
        ):
            mock_gw.resolve_context = AsyncMock(return_value=multimodal_context)
            mock_gw.process_prompt = AsyncMock(return_value=body["messages"])
            mock_gw.call_upstream = AsyncMock(
                return_value={
                    "choices": [{"message": {"content": "A cat"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
            )
            mock_gw.scan_input = AsyncMock()
            mock_gw.scan_output = AsyncMock()
            mock_client.check_quota = AsyncMock()
            mock_client.log_inference = AsyncMock()
            mock_client.track_usage = AsyncMock()

            await OrchestrationService.handle_completion(
                api_key="test-key",
                body=body,
                background_tasks=background_tasks,
            )

            mock_gw.resolve_context.assert_called_once_with(
                "test-key", "llava-7b", model_type="multimodal"
            )
