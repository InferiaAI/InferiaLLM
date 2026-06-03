"""Unit tests for _spec_from_pending mirror-injection logic (Phase 8).

Verifies that:
  - vllm + model_mirror_base set  → spec["env"]["HF_ENDPOINT"] points at the
    CP mirror's /hf pull-through endpoint.
  - ollama + model_mirror_base set → spec["model"]["artifact_uri"] is rewritten
    to the CP Ollama registry host.
  - model_mirror_base EMPTY         → no injection (upstream default unchanged).

No DB, no gRPC; the function is pure (settings monkeypatched).
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from inferia.services.orchestration.services.model_deployment.deployment_linker import (
    _spec_from_pending,
)


def _vllm_deploy(**over) -> dict:
    base = {
        "id": uuid4(),
        "engine": "vllm",
        "model_name": "my-llm",
        "inference_model": "hf://Qwen/Qwen3-0.6B",
        "configuration": json.dumps({
            "engine": "vllm",
            "artifact_uri": "hf://Qwen/Qwen3-0.6B",
        }),
        "gpu_per_replica": 1,
    }
    base.update(over)
    return base


def _ollama_deploy(**over) -> dict:
    base = {
        "id": uuid4(),
        "engine": "ollama",
        "model_name": "my-chatbot",
        "inference_model": "hf://gemma3:4b",
        "configuration": json.dumps({
            "engine": "ollama",
            "model_id": "gemma3:4b",
        }),
        "gpu_per_replica": 1,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Test 1: vllm + base set → HF_ENDPOINT injected
# ---------------------------------------------------------------------------

def test_vllm_hf_endpoint_injected(monkeypatch):
    monkeypatch.setattr(
        "inferia.services.orchestration.config.settings.model_mirror_base",
        "https://cp.example.com",
    )
    spec = _spec_from_pending(_vllm_deploy(), 1)
    assert spec["env"]["HF_ENDPOINT"] == "https://cp.example.com/hf"
    # recipe and model uri should be unchanged
    assert spec["recipe"] == "vllm"
    assert spec["model"]["artifact_uri"] == "hf://Qwen/Qwen3-0.6B"


# ---------------------------------------------------------------------------
# Test 2: ollama + base set → artifact_uri rewritten to CP registry
# ---------------------------------------------------------------------------

def test_ollama_artifact_uri_rewritten(monkeypatch):
    monkeypatch.setattr(
        "inferia.services.orchestration.config.settings.model_mirror_base",
        "https://cp.example.com",
    )
    spec = _spec_from_pending(_ollama_deploy(), 1)
    uri = spec["model"]["artifact_uri"]
    # Must route through the CP registry host
    assert "cp.example.com" in uri
    # bare name (no namespace) must get library/ prefix
    assert uri.endswith("cp.example.com/library/gemma3:4b"), (
        f"unexpected uri: {uri!r}"
    )
    # No HF_ENDPOINT for ollama
    assert "HF_ENDPOINT" not in spec.get("env", {})


# ---------------------------------------------------------------------------
# Test 3: base EMPTY → no injection (no-op)
# ---------------------------------------------------------------------------

def test_no_injection_when_base_empty(monkeypatch):
    monkeypatch.setattr(
        "inferia.services.orchestration.config.settings.model_mirror_base",
        "",
    )
    # vllm deploy
    vllm_spec = _spec_from_pending(_vllm_deploy(), 1)
    assert "HF_ENDPOINT" not in vllm_spec.get("env", {})
    assert vllm_spec["model"]["artifact_uri"] == "hf://Qwen/Qwen3-0.6B"

    # ollama deploy
    ollama_spec = _spec_from_pending(_ollama_deploy(), 1)
    assert "HF_ENDPOINT" not in ollama_spec.get("env", {})
    # artifact_uri should remain scheme-prefixed original (hf://gemma3:4b)
    assert "cp.example.com" not in ollama_spec["model"]["artifact_uri"]
