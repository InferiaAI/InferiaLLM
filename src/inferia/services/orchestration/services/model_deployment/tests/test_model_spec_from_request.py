"""Pure-unit tests for deployment_server._model_spec_from_request — the
load_model model-block for the WARM deploy path (deploy onto an already-ready
node). Mirrors the model_id/scheme fix applied to the EC2-bootstrap path so
the two paths can't diverge again.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from inferia.services.orchestration.services.model_deployment.deployment_server import (
    _model_spec_from_request,
)


def _req(**over):
    base = dict(
        configuration={"engine": "ollama", "model_id": "gemma3:4b"},
        inference_model=None,
        model_name="hjg",
        engine="ollama",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_warm_spec_resolves_ollama_model_id_with_scheme():
    spec = _model_spec_from_request(_req())
    assert spec["artifact_uri"] == "hf://gemma3:4b"
    assert spec["backend"] == "ollama"


def test_warm_spec_vllm_schemed_artifact_uri_preserved():
    spec = _model_spec_from_request(
        _req(
            engine="vllm",
            configuration={"artifact_uri": "hf://Qwen/Qwen3-0.6B"},
            model_name="qwen3-verify",
        )
    )
    assert spec["artifact_uri"] == "hf://Qwen/Qwen3-0.6B"
    assert spec["backend"] == "vllm"


def test_warm_spec_display_name_not_used_when_model_id_present():
    spec = _model_spec_from_request(_req())
    assert "hjg" not in spec["artifact_uri"]


def test_warm_spec_raises_400_when_unresolvable():
    with pytest.raises(HTTPException) as ei:
        _model_spec_from_request(
            _req(configuration={}, model_name=None, inference_model=None)
        )
    assert ei.value.status_code == 400
