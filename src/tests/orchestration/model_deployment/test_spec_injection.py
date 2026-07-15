from orchestration.models.model_deployment.deployment_linker import _spec_from_pending


def _vllm_deploy():
    return {"id": "d1", "engine": "vllm", "inference_model": "Qwen/Qwen3-0.6B",
            "model_name": "Qwen/Qwen3-0.6B", "gpu_per_replica": 1, "configuration": {}}


def test_spec_from_pending_builds_plain_spec():
    """_spec_from_pending builds a plain spec — the worker pulls model weights
    straight from origin (no CP mirror indirection).
    No env configured → empty env dict (cold path: no HF_TOKEN drop)."""
    spec = _spec_from_pending(_vllm_deploy(), 1)
    assert "HF_ENDPOINT" not in spec.get("env", {})
    # No env in configuration → empty env (explicit: empty config yields empty env)
    assert spec["env"] == {}
    assert spec["model"]["artifact_uri"] == "hf://Qwen/Qwen3-0.6B"


def test_spec_from_pending_propagates_env_cold_path():
    """Cold path (_spec_from_pending) must copy configuration.env to spec["env"].

    Task-5 injects HF_TOKEN into configuration["env"] at deploy time.
    Previously "env": {} hardcoded — gated vLLM cold deploys 401'd on the worker's
    HF pull because HF_TOKEN was silently dropped. This test locks in the fix.
    """
    deploy = {
        "id": "d3",
        "engine": "vllm",
        "inference_model": "meta-llama/Llama-3-8B-Instruct",
        "model_name": "meta-llama/Llama-3-8B-Instruct",
        "gpu_per_replica": 1,
        "configuration": {
            "env": {"HF_TOKEN": "hf_x", "FOO": "bar"},
        },
    }
    spec = _spec_from_pending(deploy, 1)
    # Full env propagation: both keys must survive to the cold-path spec.
    assert spec["env"]["HF_TOKEN"] == "hf_x"
    assert spec["env"]["FOO"] == "bar"
    assert spec["model"]["artifact_uri"] == "hf://meta-llama/Llama-3-8B-Instruct"
