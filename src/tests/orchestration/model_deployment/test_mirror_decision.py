import pytest
from orchestration.models.model_deployment.mirror_decision import (
    derive_cache_key, choose_fetch_source, apply_mirror_to_spec,
    resolve_and_apply_mirror,
)


def test_derive_cache_key_hf():
    assert derive_cache_key("vllm", "hf://Qwen/Qwen3-0.6B") == ("hf", "Qwen/Qwen3-0.6B", "main")
    assert derive_cache_key("tei", "Qwen/Qwen3-0.6B") == ("hf", "Qwen/Qwen3-0.6B", "main")


def test_derive_cache_key_ollama():
    assert derive_cache_key("ollama", "hf://gemma3:4b") == ("ollama", "gemma3", "4b")
    assert derive_cache_key("ollama", "gemma3") == ("ollama", "gemma3", "latest")
    assert derive_cache_key("ollama", "ns/m:tag") == ("ollama", "ns/m", "tag")


@pytest.mark.parametrize("status,expected", [
    ("cached", "mirror"), ("downloading", "mirror"), ("pending", "mirror"),
    ("error", "origin"), (None, "origin"),
])
def test_choose_fetch_source(status, expected):
    row = {"status": status} if status else None
    assert choose_fetch_source(row) == expected


def test_apply_mirror_hf_sets_endpoint():
    spec = {"recipe": "vllm", "model": {"artifact_uri": "hf://org/m"}, "env": {}}
    apply_mirror_to_spec(spec, recipe="vllm", mirror_base="https://cp.example/")
    assert spec["env"]["HF_ENDPOINT"] == "https://cp.example/hf"
    assert spec["model"]["artifact_uri"] == "hf://org/m"  # unchanged


def test_apply_mirror_ollama_rewrites_ref():
    spec = {"recipe": "ollama", "model": {"artifact_uri": "hf://gemma3:4b"}, "env": {}}
    apply_mirror_to_spec(spec, recipe="ollama", mirror_base="https://cp.example")
    # http:// scheme so the worker's validateArtifactURI accepts it; stripScheme
    # drops it before `ollama pull`.
    assert spec["model"]["artifact_uri"] == "http://cp.example/library/gemma3:4b"
    # The bare served name is passed so the worker re-tags after pull.
    assert spec["env"]["INFERIA_OLLAMA_SERVED_NAME"] == "gemma3:4b"
    spec2 = {"recipe": "ollama", "model": {"artifact_uri": "ns/m:tag"}, "env": {}}
    apply_mirror_to_spec(spec2, recipe="ollama", mirror_base="https://cp.example")
    assert spec2["model"]["artifact_uri"] == "http://cp.example/ns/m:tag"


def test_apply_mirror_infinity_recipe():
    spec = {"recipe": "infinity", "model": {"artifact_uri": "hf://org/m"}, "env": {}}
    apply_mirror_to_spec(spec, recipe="infinity", mirror_base="https://cp")
    assert spec["env"]["HF_ENDPOINT"] == "https://cp/hf"


class _FakeRepo:
    def __init__(self, row): self._row = row
    async def get_by_key(self, *, source, model_id, revision): return self._row


class _ErrorRepo:
    async def get_by_key(self, *, source, model_id, revision):
        raise ConnectionError("db down")


@pytest.mark.asyncio
async def test_resolve_applies_when_cached():
    spec = {"recipe": "vllm", "model": {"artifact_uri": "hf://org/m"}, "env": {}}
    await resolve_and_apply_mirror(spec, recipe="vllm", artifact_uri="hf://org/m",
                                   mirror_base="https://cp", cache_repo=_FakeRepo({"status": "cached"}))
    assert spec["env"]["HF_ENDPOINT"] == "https://cp/hf"


@pytest.mark.asyncio
async def test_resolve_skips_when_error():
    spec = {"recipe": "vllm", "model": {"artifact_uri": "hf://org/m"}, "env": {}}
    await resolve_and_apply_mirror(spec, recipe="vllm", artifact_uri="hf://org/m",
                                   mirror_base="https://cp", cache_repo=_FakeRepo({"status": "error"}))
    assert "HF_ENDPOINT" not in spec["env"]


@pytest.mark.asyncio
async def test_resolve_noop_when_base_blank():
    spec = {"recipe": "vllm", "model": {"artifact_uri": "hf://org/m"}, "env": {}}
    await resolve_and_apply_mirror(spec, recipe="vllm", artifact_uri="hf://org/m",
                                   mirror_base="", cache_repo=_FakeRepo({"status": "cached"}))
    assert "HF_ENDPOINT" not in spec["env"]


def test_resolve_noop_when_cache_repo_none():
    import asyncio
    spec = {"recipe": "vllm", "model": {"artifact_uri": "hf://org/m"}, "env": {}}
    asyncio.get_event_loop().run_until_complete(
        resolve_and_apply_mirror(spec, recipe="vllm", artifact_uri="hf://org/m",
                                 mirror_base="https://cp", cache_repo=None)
    )
    assert "HF_ENDPOINT" not in spec.get("env", {})


@pytest.mark.asyncio
async def test_resolve_swallows_lookup_error():
    spec = {"recipe": "vllm", "model": {"artifact_uri": "hf://org/m"}, "env": {}}
    await resolve_and_apply_mirror(spec, recipe="vllm", artifact_uri="hf://org/m",
                                   mirror_base="https://cp", cache_repo=_ErrorRepo())
    assert "HF_ENDPOINT" not in spec.get("env", {})
