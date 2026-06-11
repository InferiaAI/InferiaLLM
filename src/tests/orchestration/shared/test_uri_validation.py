"""
Tests for shared URI + runtime-config validators.

These tests were migrated from
inferia/services/orchestration/test/test_llmd_spec_builder.py — the spec_builder
itself is removed but the validators it contained still live here.
"""

import pytest

from orchestration.shared.uri_validation import (
    sanitize_config,
    validate_artifact_uri,
    _ALLOWED_CONFIG_KEYS,
)


class TestValidateArtifactURI:
    def test_valid_schemes_accepted(self):
        for uri in [
            "s3://bucket/path/model",
            "gs://bucket/model",
            "hf://meta-llama/Llama-3.1-8B-Instruct",
            "http://example.com/model.tar",
            "https://example.com/model.tar",
            "oci://registry.example.com/model:tag",
        ]:
            assert validate_artifact_uri(uri) == uri

    @pytest.mark.parametrize("bad", [
        "ftp://example.com/model",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/plain,abc",
        "smb://share/model",
    ])
    def test_disallowed_schemes_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_artifact_uri(bad)

    @pytest.mark.parametrize("bad", [
        "no-scheme",
        "://no-scheme",
        "://",
        "",
        "   ",
    ])
    def test_malformed_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_artifact_uri(bad)

    @pytest.mark.parametrize("bad", [
        "hf://model;rm -rf /",
        "hf://model`whoami`",
        "hf://model$(id)",
        "hf://model\nrm",
        "hf://model|cat",
        "hf://model>file",
        "hf://model<file",
        "hf://model&true",
    ])
    def test_shell_metachars_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_artifact_uri(bad)

    def test_control_chars_rejected(self):
        with pytest.raises(ValueError):
            validate_artifact_uri("hf://org/model\x00")
        with pytest.raises(ValueError):
            validate_artifact_uri("hf://org/model\x07")

    def test_non_string_rejected(self):
        with pytest.raises(ValueError):
            validate_artifact_uri(None)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            validate_artifact_uri(123)  # type: ignore[arg-type]


class TestSanitizeConfig:
    def test_empty_returns_empty(self):
        assert sanitize_config(None) == {}
        assert sanitize_config({}) == {}

    def test_allowlist_filters_unknown(self):
        cfg = {
            "tensor_parallel_size": 2,
            "dtype": "bfloat16",
            "arbitrary": "drop me",
            "trust_anything": True,
        }
        result = sanitize_config(cfg)
        assert "tensor_parallel_size" in result
        assert "dtype" in result
        assert "arbitrary" not in result
        assert "trust_anything" not in result

    def test_scalar_types_kept(self):
        cfg = {
            "tensor_parallel_size": 2,
            "gpu_memory_utilization": 0.95,
            "enforce_eager": True,
            "dtype": "auto",
        }
        result = sanitize_config(cfg)
        assert result == cfg

    def test_nonscalar_types_dropped(self):
        cfg = {
            "dtype": "bfloat16",
            "quantization": ["awq"],            # list
            "max_num_seqs": {"nested": "v"},    # dict
            "max_model_len": None,              # None
        }
        result = sanitize_config(cfg)
        assert result == {"dtype": "bfloat16"}

    def test_all_allowed_keys_pass(self):
        # Every allowed key with a safe value should round-trip.
        cfg = {key: "value" for key in _ALLOWED_CONFIG_KEYS}
        result = sanitize_config(cfg)
        assert set(result.keys()) == _ALLOWED_CONFIG_KEYS
