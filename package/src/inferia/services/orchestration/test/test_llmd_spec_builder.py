"""
Tests for K8s CRD spec builder input validation.

Verifies that:
1. artifact_uri is validated against allowed schemes and safe characters
2. config dict is filtered to allowed keys with safe scalar values only
3. Malicious inputs are rejected or stripped before reaching the CRD spec
"""

import pytest

from inferia.services.orchestration.services.llmd.spec_builder import (
    build_llmd_spec,
    _validate_artifact_uri,
    _sanitize_config,
)


def _make_model(uri="hf://org/model", backend="vllm", config=None):
    return {"artifact_uri": uri, "backend": backend, "config": config}


# ---------------------------------------------------------------------------
# artifact_uri validation
# ---------------------------------------------------------------------------

class TestArtifactUriValidation:
    """Verify artifact_uri scheme and character validation."""

    def test_valid_hf_uri(self):
        assert _validate_artifact_uri("hf://meta-llama/Llama-3-8B") == "hf://meta-llama/Llama-3-8B"

    def test_valid_s3_uri(self):
        assert _validate_artifact_uri("s3://bucket/path/to/model") == "s3://bucket/path/to/model"

    def test_valid_gs_uri(self):
        assert _validate_artifact_uri("gs://bucket/model") == "gs://bucket/model"

    def test_valid_https_uri(self):
        assert _validate_artifact_uri("https://huggingface.co/org/model") == "https://huggingface.co/org/model"

    def test_valid_oci_uri(self):
        assert _validate_artifact_uri("oci://registry/image:tag") == "oci://registry/image:tag"

    def test_rejects_file_scheme(self):
        with pytest.raises(ValueError, match="not in allowed list"):
            _validate_artifact_uri("file:///etc/shadow")

    def test_rejects_ftp_scheme(self):
        with pytest.raises(ValueError, match="not in allowed list"):
            _validate_artifact_uri("ftp://attacker.com/payload")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_artifact_uri("")

    def test_rejects_none(self):
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_artifact_uri(None)

    def test_rejects_shell_metachar_backtick(self):
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_artifact_uri("hf://org/`whoami`")

    def test_rejects_shell_metachar_dollar(self):
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_artifact_uri("hf://org/${EXPLOIT}")

    def test_rejects_shell_metachar_semicolon(self):
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_artifact_uri("hf://org/model;rm -rf /")

    def test_rejects_shell_metachar_pipe(self):
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_artifact_uri("hf://org/model|cat /etc/passwd")

    def test_rejects_no_scheme(self):
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_artifact_uri("/etc/passwd")


# ---------------------------------------------------------------------------
# config sanitization
# ---------------------------------------------------------------------------

class TestConfigSanitization:
    """Verify config dict is filtered to allowed keys and safe values."""

    def test_allowed_keys_pass_through(self):
        config = {
            "tensor_parallel_size": 2,
            "dtype": "float16",
            "gpu_memory_utilization": 0.9,
        }
        result = _sanitize_config(config)
        assert result == config

    def test_unknown_keys_stripped(self):
        config = {
            "tensor_parallel_size": 2,
            "evil_key": "malicious_value",
            "apiVersion": "hacked",
        }
        result = _sanitize_config(config)
        assert result == {"tensor_parallel_size": 2}
        assert "evil_key" not in result
        assert "apiVersion" not in result

    def test_nested_dict_values_stripped(self):
        """Nested dicts could inject arbitrary CRD structure."""
        config = {
            "tensor_parallel_size": 2,
            "dtype": {"nested": "attack"},
        }
        result = _sanitize_config(config)
        assert result == {"tensor_parallel_size": 2}

    def test_list_values_stripped(self):
        """Lists could inject array structures into the CRD."""
        config = {
            "tensor_parallel_size": 2,
            "dtype": ["attack1", "attack2"],
        }
        result = _sanitize_config(config)
        assert result == {"tensor_parallel_size": 2}

    def test_none_config(self):
        assert _sanitize_config(None) == {}

    def test_empty_config(self):
        assert _sanitize_config({}) == {}

    def test_bool_values_allowed(self):
        config = {"enforce_eager": True, "trust_remote_code": False}
        result = _sanitize_config(config)
        assert result == config

    def test_backend_override_blocked(self):
        """Config must not be able to override the 'backend' key in runtime."""
        config = {"backend": "attacker-controlled"}
        result = _sanitize_config(config)
        assert "backend" not in result


# ---------------------------------------------------------------------------
# build_llmd_spec integration
# ---------------------------------------------------------------------------

class TestBuildLlmdSpec:
    """Verify the full spec builder uses validation."""

    def test_valid_model_produces_spec(self):
        model = _make_model(
            uri="hf://meta-llama/Llama-3-8B",
            config={"tensor_parallel_size": 2},
        )
        spec = build_llmd_spec(
            deployment_id="dep-123",
            model=model,
            replicas=1,
            gpu_per_replica=1,
            node_names=["node-1"],
        )
        assert spec["spec"]["model"]["uri"] == "hf://meta-llama/Llama-3-8B"
        assert spec["spec"]["runtime"]["tensor_parallel_size"] == 2
        assert spec["spec"]["runtime"]["backend"] == "vllm"

    def test_malicious_uri_rejected(self):
        model = _make_model(uri="file:///etc/shadow")
        with pytest.raises(ValueError):
            build_llmd_spec(
                deployment_id="dep-123",
                model=model,
                replicas=1,
                gpu_per_replica=1,
                node_names=["node-1"],
            )

    def test_malicious_config_keys_stripped(self):
        model = _make_model(config={
            "tensor_parallel_size": 2,
            "apiVersion": "hacked",
            "metadata": {"name": "evil"},
        })
        spec = build_llmd_spec(
            deployment_id="dep-123",
            model=model,
            replicas=1,
            gpu_per_replica=1,
            node_names=["node-1"],
        )
        runtime = spec["spec"]["runtime"]
        assert runtime["tensor_parallel_size"] == 2
        assert "apiVersion" not in runtime
        assert "metadata" not in runtime

    def test_config_cannot_override_backend(self):
        model = _make_model(
            backend="vllm",
            config={"backend": "evil-backend"},
        )
        spec = build_llmd_spec(
            deployment_id="dep-123",
            model=model,
            replicas=1,
            gpu_per_replica=1,
            node_names=["node-1"],
        )
        # backend comes from model["backend"], config's "backend" is stripped
        assert spec["spec"]["runtime"]["backend"] == "vllm"
