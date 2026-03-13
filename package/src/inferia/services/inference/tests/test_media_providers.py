"""Tests for media provider adapters — image, video, audio generation."""

import pytest


class TestImageAdapter:
    def test_chat_path_returns_images_endpoint(self):
        from inferia.services.inference.core.providers import ImageAdapter
        adapter = ImageAdapter()
        assert adapter.get_chat_path() == "/v1/images/generations"

    def test_is_internal(self):
        from inferia.services.inference.core.providers import ImageAdapter
        adapter = ImageAdapter()
        assert adapter.is_external() is False

    def test_headers_include_bearer_token(self):
        from inferia.services.inference.core.providers import ImageAdapter
        adapter = ImageAdapter()
        headers = adapter.get_headers("test-key")
        assert headers["Authorization"] == "Bearer test-key"

    def test_transform_request_passthrough(self):
        from inferia.services.inference.core.providers import ImageAdapter
        adapter = ImageAdapter()
        payload = {"model": "sdxl", "prompt": "a cat", "n": 1, "size": "1024x1024"}
        result = adapter.transform_request(payload)
        assert result == payload

    def test_transform_response_passthrough(self):
        from inferia.services.inference.core.providers import ImageAdapter
        adapter = ImageAdapter()
        response = {"created": 123, "data": [{"url": "http://img.png"}]}
        result = adapter.transform_response(response)
        assert result == response


class TestVideoAdapter:
    def test_chat_path_returns_videos_endpoint(self):
        from inferia.services.inference.core.providers import VideoAdapter
        adapter = VideoAdapter()
        assert adapter.get_chat_path() == "/v1/videos/generations"

    def test_is_internal(self):
        from inferia.services.inference.core.providers import VideoAdapter
        adapter = VideoAdapter()
        assert adapter.is_external() is False


class TestAudioAdapter:
    def test_chat_path_returns_speech_endpoint(self):
        from inferia.services.inference.core.providers import AudioAdapter
        adapter = AudioAdapter()
        assert adapter.get_chat_path() == "/v1/audio/speech"

    def test_transcription_path(self):
        from inferia.services.inference.core.providers import AudioAdapter
        adapter = AudioAdapter()
        assert adapter.get_transcription_path() == "/v1/audio/transcriptions"

    def test_is_internal(self):
        from inferia.services.inference.core.providers import AudioAdapter
        adapter = AudioAdapter()
        assert adapter.is_external() is False


class TestGetAdapterFactory:
    """Test the get_adapter factory with new engines."""

    def test_diffusers_returns_image_adapter(self):
        from inferia.services.inference.core.providers import get_adapter, ImageAdapter
        adapter = get_adapter("diffusers")
        assert isinstance(adapter, ImageAdapter)

    def test_sdxl_returns_image_adapter(self):
        from inferia.services.inference.core.providers import get_adapter, ImageAdapter
        adapter = get_adapter("sdxl")
        assert isinstance(adapter, ImageAdapter)

    def test_comfyui_returns_image_adapter(self):
        from inferia.services.inference.core.providers import get_adapter, ImageAdapter
        adapter = get_adapter("comfyui")
        assert isinstance(adapter, ImageAdapter)

    def test_diffusers_video_returns_video_adapter(self):
        from inferia.services.inference.core.providers import get_adapter, VideoAdapter
        adapter = get_adapter("diffusers-video")
        assert isinstance(adapter, VideoAdapter)

    def test_modelscope_returns_video_adapter(self):
        from inferia.services.inference.core.providers import get_adapter, VideoAdapter
        adapter = get_adapter("modelscope")
        assert isinstance(adapter, VideoAdapter)

    def test_whisper_returns_audio_adapter(self):
        from inferia.services.inference.core.providers import get_adapter, AudioAdapter
        adapter = get_adapter("whisper")
        assert isinstance(adapter, AudioAdapter)

    def test_bark_returns_audio_adapter(self):
        from inferia.services.inference.core.providers import get_adapter, AudioAdapter
        adapter = get_adapter("bark")
        assert isinstance(adapter, AudioAdapter)

    def test_tts_returns_audio_adapter(self):
        from inferia.services.inference.core.providers import get_adapter, AudioAdapter
        adapter = get_adapter("tts")
        assert isinstance(adapter, AudioAdapter)

    def test_localai_default_returns_compute_adapter(self):
        from inferia.services.inference.core.providers import get_adapter, ComputeAdapter
        adapter = get_adapter("localai")
        assert isinstance(adapter, ComputeAdapter)

    def test_localai_with_image_type_returns_image_adapter(self):
        from inferia.services.inference.core.providers import get_adapter, ImageAdapter
        adapter = get_adapter("localai", model_type="image_generation")
        assert isinstance(adapter, ImageAdapter)

    def test_localai_with_audio_type_returns_audio_adapter(self):
        from inferia.services.inference.core.providers import get_adapter, AudioAdapter
        adapter = get_adapter("localai", model_type="audio_generation")
        assert isinstance(adapter, AudioAdapter)

    def test_localai_with_video_type_returns_video_adapter(self):
        from inferia.services.inference.core.providers import get_adapter, VideoAdapter
        adapter = get_adapter("localai", model_type="video_generation")
        assert isinstance(adapter, VideoAdapter)

    def test_localai_with_embedding_type_returns_embedding_adapter(self):
        from inferia.services.inference.core.providers import get_adapter, EmbeddingAdapter
        adapter = get_adapter("localai", model_type="embedding")
        assert isinstance(adapter, EmbeddingAdapter)

    def test_localai_with_inference_type_returns_compute_adapter(self):
        from inferia.services.inference.core.providers import get_adapter, ComputeAdapter
        adapter = get_adapter("localai", model_type="inference")
        assert isinstance(adapter, ComputeAdapter)


class TestEngineCategories:
    """Test that new engines are in COMPUTE_ENGINES (not external)."""

    @pytest.mark.parametrize("engine", [
        "diffusers", "sdxl", "comfyui",
        "diffusers-video", "modelscope",
        "whisper", "bark", "tts",
        "localai",
    ])
    def test_media_engines_are_compute(self, engine):
        from inferia.services.inference.core.providers import COMPUTE_ENGINES
        assert engine in COMPUTE_ENGINES

    @pytest.mark.parametrize("engine", [
        "diffusers", "sdxl", "comfyui",
        "diffusers-video", "modelscope",
        "whisper", "bark", "tts",
        "localai",
    ])
    def test_media_engines_not_external(self, engine):
        from inferia.services.inference.core.providers import is_external_engine
        assert is_external_engine(engine) is False
