"""Tests for expanded injection vectors in job/SDL builders."""

import shlex
import yaml
from unittest.mock import patch

import pytest

# Patch settings before importing job_builder
_mock_settings = type("Settings", (), {"internal_api_key": "test-key"})()

with patch("inferia.services.orchestration.config.settings", _mock_settings):
    from inferia.services.orchestration.services.adapter_engine.adapters.nosana.job_builder import (
        create_vllm_job,
        create_training_job,
    )

from inferia.services.orchestration.services.adapter_engine.adapters.akash.sdl_builder import (
    build_inference_sdl,
    build_training_sdl,
)


class TestEnvVarInjection:
    """Test environment variable expansion injection."""

    def test_dollar_var_in_model_id_not_expanded(self):
        """${VAR} and $VAR in model_id should be quoted, not expanded."""
        payloads = ["${HOME}/stolen", "$PATH:/evil", "$(whoami)"]
        for payload in payloads:
            result = create_vllm_job(model_id=payload)
            cmd = result["op"]["args"]["cmd"]
            # model_id appears in cmd args — it should be the literal string
            cmd_str = " ".join(cmd)
            # The model_id is used as a CLI argument, not in a shell context
            # Verify it appears literally in the args
            assert payload in cmd_str

    def test_env_var_in_training_git_repo_quoted(self):
        """$VAR in git_repo is shlex.quoted in training jobs."""
        payload = "${SECRET_KEY}"
        result = create_training_job(
            image="pytorch:latest",
            training_script="train.py",
            git_repo=payload,
        )
        cmd = result["ops"][0]["args"]["cmd"]
        cmd_str = cmd[-1] if isinstance(cmd, list) else cmd
        assert shlex.quote(payload) in cmd_str


class TestUnicodeHomoglyph:
    """Test unicode homoglyph injection in model names."""

    def test_unicode_in_sdl_image_preserved(self):
        """Unicode characters in image name don't break SDL YAML."""
        # Cyrillic 'а' looks like Latin 'a'
        image = "nginx\u0430:latest"
        sdl = build_inference_sdl(image=image)
        parsed = yaml.safe_load(sdl)
        assert parsed["services"]["app"]["image"] == image

    def test_unicode_in_model_id(self):
        """Unicode in vLLM model_id is passed through safely."""
        model = "meta-llama/Llаmа-2-7b"  # Cyrillic 'а'
        result = create_vllm_job(model_id=model)
        cmd = result["op"]["args"]["cmd"]
        # Should contain the model ID somewhere in the command
        assert model in cmd


class TestNullByteInjection:
    """Test null byte injection vectors."""

    def test_null_byte_in_sdl_env(self):
        """Null bytes in env vars don't corrupt SDL YAML structure."""
        env = {"KEY": "value\x00injected"}
        sdl = build_inference_sdl(image="nginx:latest", env=env)
        # YAML dump should succeed and produce valid YAML
        parsed = yaml.safe_load(sdl)
        assert "services" in parsed

    def test_newline_in_sdl_env_value(self):
        """Newline chars in env values don't break YAML structure."""
        env = {"KEY": "line1\nline2\nline3"}
        sdl = build_inference_sdl(image="nginx:latest", env=env)
        parsed = yaml.safe_load(sdl)
        envs = parsed["services"]["app"]["env"]
        # The env entry should contain the newlines in a single entry
        matching = [e for e in envs if e.startswith("KEY=")]
        assert len(matching) == 1


class TestYAMLInjection:
    """Test YAML structure injection in SDL builder."""

    def test_yaml_value_injection_in_env(self):
        """YAML special characters in env values don't inject structure."""
        env = {"KEY": "value: [injected, yaml]\n  nested: true"}
        sdl = build_inference_sdl(image="nginx:latest", env=env)
        parsed = yaml.safe_load(sdl)
        # The env should still be a flat list of strings
        envs = parsed["services"]["app"]["env"]
        assert all(isinstance(e, str) for e in envs)

    def test_image_tag_with_semicolon(self):
        """Image name with shell metacharacters doesn't execute."""
        image = "nginx:latest;rm -rf /"
        sdl = build_inference_sdl(image=image)
        parsed = yaml.safe_load(sdl)
        # Image should be stored as-is (Akash validates it)
        assert parsed["services"]["app"]["image"] == image
