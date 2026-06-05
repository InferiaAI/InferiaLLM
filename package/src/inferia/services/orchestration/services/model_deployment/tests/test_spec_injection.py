import pytest
from inferia.services.orchestration.services.model_deployment.deployment_linker import _spec_from_pending
from inferia.services.orchestration.services.model_deployment.mirror_decision import resolve_and_apply_mirror


def _vllm_deploy():
    return {"id": "d1", "engine": "vllm", "inference_model": "Qwen/Qwen3-0.6B",
            "model_name": "Qwen/Qwen3-0.6B", "gpu_per_replica": 1, "configuration": {}}


def _ollama_deploy():
    return {"id": "d2", "engine": "ollama", "inference_model": "gemma3:4b",
            "model_name": "gemma3:4b", "gpu_per_replica": 1,
            "configuration": {"model_id": "gemma3:4b"}}


class _FakeRepo:
    def __init__(self, row): self._row = row
    async def get_by_key(self, *, source, model_id, revision): return self._row


def test_spec_from_pending_has_no_inline_injection():
    """_spec_from_pending now builds a plain spec; mirror injection is applied
    separately by resolve_and_apply_mirror in on_worker_ready."""
    spec = _spec_from_pending(_vllm_deploy(), 1)
    assert "HF_ENDPOINT" not in spec.get("env", {})
    assert spec["env"] == {}
    assert spec["model"]["artifact_uri"] == "hf://Qwen/Qwen3-0.6B"


@pytest.mark.asyncio
async def test_injection_applied_when_cached():
    spec = _spec_from_pending(_vllm_deploy(), 1)
    await resolve_and_apply_mirror(spec, recipe=spec["recipe"],
        artifact_uri=spec["model"]["artifact_uri"], mirror_base="https://cp",
        cache_repo=_FakeRepo({"status": "cached"}))
    assert spec["env"]["HF_ENDPOINT"] == "https://cp/hf"


@pytest.mark.asyncio
async def test_injection_skipped_when_error():
    spec = _spec_from_pending(_vllm_deploy(), 1)
    await resolve_and_apply_mirror(spec, recipe=spec["recipe"],
        artifact_uri=spec["model"]["artifact_uri"], mirror_base="https://cp",
        cache_repo=_FakeRepo({"status": "error"}))
    assert "HF_ENDPOINT" not in spec.get("env", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("mirror_status", ["downloading", "pending"])
async def test_injection_applied_when_in_progress(mirror_status):
    """Mirror injection fires for 'downloading' and 'pending' states, not just
    'cached': the worker should pull through the CP mirror while it pre-warms."""
    spec = _spec_from_pending(_vllm_deploy(), 1)
    await resolve_and_apply_mirror(spec, recipe=spec["recipe"],
        artifact_uri=spec["model"]["artifact_uri"], mirror_base="https://cp",
        cache_repo=_FakeRepo({"status": mirror_status}))
    assert spec["env"]["HF_ENDPOINT"] == "https://cp/hf"


@pytest.mark.asyncio
async def test_ollama_mirror_rewrites_artifact_uri():
    """For ollama deploys the mirror rewrite changes artifact_uri (not env).

    _spec_from_pending with configuration={'model_id': 'gemma3:4b'} produces
    artifact_uri='hf://gemma3:4b' (bare ref, no slash after scheme-strip).
    apply_mirror_to_spec prepends '<host>/library/' for bare (non-namespaced)
    refs, yielding 'cp/library/gemma3:4b'. HF_ENDPOINT must NOT be set because
    ollama uses a registry pull, not huggingface_hub.
    """
    spec = _spec_from_pending(_ollama_deploy(), 1)
    # Confirm pre-rewrite value (bare ref with hf:// scheme).
    assert spec["model"]["artifact_uri"] == "hf://gemma3:4b"
    assert spec["recipe"] == "ollama"

    await resolve_and_apply_mirror(
        spec, recipe=spec["recipe"],
        artifact_uri=spec["model"]["artifact_uri"],
        mirror_base="https://cp",
        cache_repo=_FakeRepo({"status": "cached"}),
    )

    # After rewrite: http:// scheme (worker validateArtifactURI), library/
    # prefix for the bare ref.
    assert spec["model"]["artifact_uri"] == "http://cp/library/gemma3:4b"
    # Ollama uses registry pull; HF_ENDPOINT must not be injected.
    assert "HF_ENDPOINT" not in spec.get("env", {})
