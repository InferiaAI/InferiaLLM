"""Unit tests for resolve_artifact_uri — the single source of truth for
turning a deployment's persisted fields into the worker's artifact_uri.

Regression target: ollama deploys persist the real model tag in
``configuration.model_id`` (e.g. "gemma3:4b") with NO scheme, while the
spec builders previously read only ``model.artifact_uri`` / ``artifact_uri`` /
``model_name`` — so they shipped the display name (e.g. "hjg"), which the
worker's recipe rejected (no scheme) → deploy FAILED. See
docs/plans/2026-05-31-deploy-ec2-visibility-modelload-delete.md.
"""
from __future__ import annotations

import pytest

from orchestration.model_deployment.model_ref import (
    resolve_artifact_uri,
)


def test_ollama_model_id_wins_over_display_name():
    # The killer bug: model_name is the display name "hjg"; the real tag
    # lives in configuration.model_id. Must resolve the tag, schemed.
    uri = resolve_artifact_uri(
        configuration={"engine": "ollama", "model_id": "gemma3:4b"},
        model_name="hjg",
    )
    assert uri == "hf://gemma3:4b"


def test_schemed_artifact_uri_passes_through_unchanged():
    uri = resolve_artifact_uri(
        configuration={"artifact_uri": "hf://Qwen/Qwen3-0.6B"},
        model_name="qwen3-verify",
    )
    assert uri == "hf://Qwen/Qwen3-0.6B"


def test_nested_model_artifact_uri_has_highest_priority():
    uri = resolve_artifact_uri(
        configuration={
            "model": {"artifact_uri": "s3://bucket/model"},
            "artifact_uri": "hf://ignored",
            "model_id": "ignored:tag",
        },
        model_name="x",
    )
    assert uri == "s3://bucket/model"


def test_schemeless_inference_model_gets_hf_scheme():
    uri = resolve_artifact_uri(
        configuration={},
        inference_model="org/model",
        model_name="display",
    )
    assert uri == "hf://org/model"


def test_schemeless_model_name_fallback_gets_scheme():
    uri = resolve_artifact_uri(configuration={}, model_name="my-model")
    assert uri == "hf://my-model"


def test_returns_none_when_nothing_resolvable():
    assert resolve_artifact_uri(configuration={}) is None
    assert resolve_artifact_uri(configuration=None) is None


def test_already_schemed_model_id_is_not_double_prefixed():
    uri = resolve_artifact_uri(
        configuration={"model_id": "hf://foo/bar"}, model_name="d",
    )
    assert uri == "hf://foo/bar"


def test_configuration_none_falls_back_to_model_name():
    uri = resolve_artifact_uri(configuration=None, model_name="solo")
    assert uri == "hf://solo"


def test_non_dict_model_block_is_ignored_gracefully():
    # asyncpg / bad input could hand us a string where a dict is expected.
    uri = resolve_artifact_uri(
        configuration={"model": "not-a-dict", "model_id": "llama3:8b"},
        model_name="d",
    )
    assert uri == "hf://llama3:8b"


def test_whitespace_is_stripped_before_scheme_decision():
    uri = resolve_artifact_uri(
        configuration={"model_id": "  gemma3:4b  "}, model_name="d",
    )
    assert uri == "hf://gemma3:4b"


def test_namespaced_ollama_tag_is_schemed_not_split():
    uri = resolve_artifact_uri(
        configuration={"model_id": "mannix/llama3.1-8b"}, model_name="d",
    )
    assert uri == "hf://mannix/llama3.1-8b"


@pytest.mark.parametrize("scheme", ["s3", "gs", "hf", "http", "https", "oci"])
def test_all_worker_allowed_schemes_pass_through(scheme):
    uri = resolve_artifact_uri(
        configuration={"artifact_uri": f"{scheme}://path/to/model"},
    )
    assert uri == f"{scheme}://path/to/model"
