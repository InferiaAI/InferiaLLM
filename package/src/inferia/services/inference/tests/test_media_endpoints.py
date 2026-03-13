"""Tests for media generation API endpoints and URL building."""

import pytest


class TestBuildFullUrlForMediaPaths:
    """Test that _build_full_url handles new media path patterns."""

    def test_images_path(self):
        from inferia.services.inference.core.service import GatewayService
        url = GatewayService._build_full_url(
            "http://host:9000", "/v1/images/generations"
        )
        assert url == "http://host:9000/v1/images/generations"

    def test_videos_path(self):
        from inferia.services.inference.core.service import GatewayService
        url = GatewayService._build_full_url(
            "http://host:9000", "/v1/videos/generations"
        )
        assert url == "http://host:9000/v1/videos/generations"

    def test_audio_speech_path(self):
        from inferia.services.inference.core.service import GatewayService
        url = GatewayService._build_full_url(
            "http://host:9000", "/v1/audio/speech"
        )
        assert url == "http://host:9000/v1/audio/speech"

    def test_audio_transcriptions_path(self):
        from inferia.services.inference.core.service import GatewayService
        url = GatewayService._build_full_url(
            "http://host:9000", "/v1/audio/transcriptions"
        )
        assert url == "http://host:9000/v1/audio/transcriptions"

    def test_endpoint_already_has_images_path(self):
        from inferia.services.inference.core.service import GatewayService
        url = GatewayService._build_full_url(
            "http://host:9000/v1/images/generations", "/v1/images/generations"
        )
        assert url == "http://host:9000/v1/images/generations"

    def test_v1_endpoint_with_images_path(self):
        from inferia.services.inference.core.service import GatewayService
        url = GatewayService._build_full_url(
            "http://host:9000/v1", "/v1/images/generations"
        )
        assert url == "http://host:9000/v1/images/generations"
        assert "v1/v1" not in url

    def test_v1_endpoint_with_audio_speech_path(self):
        from inferia.services.inference.core.service import GatewayService
        url = GatewayService._build_full_url(
            "http://host:9000/v1", "/v1/audio/speech"
        )
        assert url == "http://host:9000/v1/audio/speech"
        assert "v1/v1" not in url

    def test_endpoint_already_has_audio_transcriptions_path(self):
        from inferia.services.inference.core.service import GatewayService
        url = GatewayService._build_full_url(
            "http://host:9000/v1/audio/transcriptions", "/v1/audio/transcriptions"
        )
        assert url == "http://host:9000/v1/audio/transcriptions"
