from orchestration.messaging.uri_validation import sanitize_config
from common.model_types import ModelType, ModelCapabilities


def test_sanitize_config_preserves_diffusion_keys():
    cfg = {
        "model_type": "video_generation",
        "trust_remote_code": True,
        "model_offload": True,
        "group_offload": False,
        "bogus_key": "dropme",
    }
    out = sanitize_config(cfg)
    assert out["model_type"] == "video_generation"
    assert out["trust_remote_code"] is True
    assert out["model_offload"] is True
    assert out["group_offload"] is False
    assert "bogus_key" not in out


def test_image_generation_supports_inferia_diffusion():
    backends = ModelCapabilities.get_supported_backends(ModelType.IMAGE_GENERATION)
    assert "inferia-diffusion" in backends
    vbackends = ModelCapabilities.get_supported_backends(ModelType.VIDEO_GENERATION)
    assert "inferia-diffusion" in vbackends
