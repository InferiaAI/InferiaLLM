"""The warm deploy path must apply the same mirror decision as the cold path."""
from __future__ import annotations
from orchestration.models.model_deployment.mirror_decision import (
    derive_cache_key, choose_fetch_source, apply_mirror_to_spec,
)


def test_warm_style_spec_gets_hf_endpoint_when_cached():
    spec = {"recipe": "vllm", "model": {"artifact_uri": "hf://org/m"}, "env": {}}
    src, mid, rev = derive_cache_key(spec["recipe"], spec["model"]["artifact_uri"])
    assert (src, mid, rev) == ("hf", "org/m", "main")
    if choose_fetch_source({"status": "downloading"}) == "mirror":
        apply_mirror_to_spec(spec, recipe=spec["recipe"], mirror_base="https://cp")
    assert spec["env"]["HF_ENDPOINT"] == "https://cp/hf"


def test_warm_style_spec_origin_when_error():
    spec = {"recipe": "vllm", "model": {"artifact_uri": "hf://org/m"}, "env": {}}
    if choose_fetch_source({"status": "error"}) == "mirror":
        apply_mirror_to_spec(spec, recipe=spec["recipe"], mirror_base="https://cp")
    assert "HF_ENDPOINT" not in spec["env"]
