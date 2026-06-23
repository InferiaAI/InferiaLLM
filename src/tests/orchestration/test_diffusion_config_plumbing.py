import json

from orchestration.messaging.uri_validation import sanitize_config
from common.model_types import ModelType, ModelCapabilities
from orchestration.models.model_deployment.deployment_linker import _spec_from_pending


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


def test_spec_from_pending_forwards_diffusion_config_and_env():
    # Mirrors the dashboard-emitted configuration for an inferia-diffusion deploy:
    # diffusion options nested under `config`, HF_TOKEN injected server-side into
    # `env` from the named token. Both must reach the worker load spec intact.
    configuration = json.dumps({
        "model_id": "stabilityai/sdxl-turbo",
        "engine": "inferia-diffusion",
        "config": {"model_type": "image_generation", "trust_remote_code": True},
        "env": {"HF_TOKEN": "secret123"},
    })
    deploy = {
        "id": "00000000-0000-0000-0000-000000000001",
        "engine": "inferia-diffusion",
        "inference_model": "stabilityai/sdxl-turbo",
        "model_name": "my-image-deploy",
        "configuration": configuration,
    }
    spec = _spec_from_pending(deploy, gpu_required=1)
    assert spec["recipe"] == "inferia-diffusion"
    assert spec["config"]["model_type"] == "image_generation"
    assert spec["config"]["trust_remote_code"] is True
    assert spec["env"]["HF_TOKEN"] == "secret123"
    assert spec["model"]["artifact_uri"].endswith("stabilityai/sdxl-turbo")
