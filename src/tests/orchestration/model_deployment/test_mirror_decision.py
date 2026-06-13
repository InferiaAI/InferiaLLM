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


def test_apply_mirror_path_suffixed_base_single_port():
    """When INFERIA_MODEL_MIRROR_BASE carries a path (e.g. https://h.example/api
    in single-port mode), HF_ENDPOINT must keep the full base+/hf, but the
    ollama registry host must be the origin (h.example) — NOT h.example/api —
    so that `ollama pull` hits /v2 at root, not /v2/api/..."""
    # HF branch: base+/hf preserved (no change expected)
    spec_hf = {"recipe": "vllm", "model": {"artifact_uri": "hf://org/m"}, "env": {}}
    apply_mirror_to_spec(spec_hf, recipe="vllm", mirror_base="https://h.example/api")
    assert spec_hf["env"]["HF_ENDPOINT"] == "https://h.example/api/hf"

    # Ollama branch: artifact_uri host must be h.example (origin netloc), no /api
    spec_ol = {"recipe": "ollama", "model": {"artifact_uri": "gemma3:4b"}, "env": {}}
    apply_mirror_to_spec(spec_ol, recipe="ollama", mirror_base="https://h.example/api")
    uri = spec_ol["model"]["artifact_uri"]
    # host component must not include the /api path segment
    assert "h.example/api" not in uri, f"path leaked into ollama host: {uri!r}"
    assert uri.startswith("http://h.example/"), f"wrong host in ollama uri: {uri!r}"
    assert "library/gemma3:4b" in uri, f"model ref missing: {uri!r}"
    assert spec_ol["env"]["INFERIA_OLLAMA_SERVED_NAME"] == "gemma3:4b"

    # Bare base (no path): existing behaviour must be unchanged
    spec_bare = {"recipe": "ollama", "model": {"artifact_uri": "gemma3:4b"}, "env": {}}
    apply_mirror_to_spec(spec_bare, recipe="ollama", mirror_base="https://cp.example")
    assert spec_bare["model"]["artifact_uri"] == "http://cp.example/library/gemma3:4b"


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


@pytest.mark.asyncio
async def test_resolve_noop_when_cache_repo_none():
    spec = {"recipe": "vllm", "model": {"artifact_uri": "hf://org/m"}, "env": {}}
    await resolve_and_apply_mirror(spec, recipe="vllm", artifact_uri="hf://org/m",
                                   mirror_base="https://cp", cache_repo=None)
    assert "HF_ENDPOINT" not in spec.get("env", {})


@pytest.mark.asyncio
async def test_resolve_swallows_lookup_error():
    spec = {"recipe": "vllm", "model": {"artifact_uri": "hf://org/m"}, "env": {}}
    await resolve_and_apply_mirror(spec, recipe="vllm", artifact_uri="hf://org/m",
                                   mirror_base="https://cp", cache_repo=_ErrorRepo())
    assert "HF_ENDPOINT" not in spec.get("env", {})
