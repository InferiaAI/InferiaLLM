"""Pure-unit tests for deployment_linker._spec_from_pending — the load_model
spec the EC2-bootstrap path (on_worker_ready) sends to a freshly-registered
worker. This is the path an empty-pool gemma3:4b deploy travels.

Regression: the spec must carry the REAL model tag (configuration.model_id),
scheme-prefixed, not the human display name (model_name).
"""
from __future__ import annotations

import json
from uuid import uuid4

from orchestration.models.model_deployment.deployment_linker import (
    _spec_from_pending,
)


def _deploy(**over):
    base = {
        "id": uuid4(),
        "engine": "ollama",
        "model_name": "hjg",  # human display name — must NOT become the uri
        "configuration": {"engine": "ollama", "model_id": "gemma3:4b"},
        "gpu_per_replica": 1,
    }
    base.update(over)
    return base


def test_ollama_model_id_becomes_schemed_artifact_uri():
    spec = _spec_from_pending(_deploy(), 1)
    assert spec["recipe"] == "ollama"
    assert spec["model"]["artifact_uri"] == "hf://gemma3:4b"
    assert spec["deployment_id"]
    assert spec["gpu_indices"] == [0]
    assert spec["port"] == 0


def test_jsonstring_configuration_is_parsed_then_resolved():
    # asyncpg hands jsonb columns back as a str without a codec.
    spec = _spec_from_pending(
        _deploy(configuration=json.dumps({"model_id": "gemma3:4b"})), 1,
    )
    assert spec["model"]["artifact_uri"] == "hf://gemma3:4b"


def test_vllm_schemed_artifact_uri_preserved():
    spec = _spec_from_pending(
        _deploy(
            engine="vllm",
            model_name="qwen3-verify",
            configuration={"artifact_uri": "hf://Qwen/Qwen3-0.6B"},
        ),
        1,
    )
    assert spec["recipe"] == "vllm"
    assert spec["model"]["artifact_uri"] == "hf://Qwen/Qwen3-0.6B"


def test_gpu_indices_track_required_count():
    spec = _spec_from_pending(_deploy(gpu_per_replica=2), 2)
    assert spec["gpu_indices"] == [0, 1]


def test_display_name_is_not_used_when_model_id_present():
    spec = _spec_from_pending(_deploy(), 1)
    assert "hjg" not in spec["model"]["artifact_uri"]
