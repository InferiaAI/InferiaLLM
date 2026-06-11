"""Tests for SDL template structure validation."""

import yaml

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.akash.sdl_builder import (
    build_inference_sdl,
    build_training_sdl,
)


class TestSDLStructure:
    """Verify SDL builder produces valid, safe YAML."""

    def test_resource_values_are_numeric(self):
        """GPU units and CPU units in SDL are numeric types."""
        sdl = build_inference_sdl(
            image="vllm:latest", gpu_units=2, cpu_units=4.0
        )
        parsed = yaml.safe_load(sdl)
        resources = parsed["profiles"]["compute"]["app"]["resources"]
        assert isinstance(resources["gpu"]["units"], int)
        assert isinstance(resources["cpu"]["units"], (int, float))

    def test_port_present_in_expose(self):
        """Configured port appears in service expose section."""
        sdl = build_inference_sdl(image="vllm:latest", port=9000)
        parsed = yaml.safe_load(sdl)
        expose = parsed["services"]["app"]["expose"]
        assert expose[0]["port"] == 9000

    def test_gpu_units_positive(self):
        """GPU units value preserved correctly."""
        sdl = build_inference_sdl(image="vllm:latest", gpu_units=4)
        parsed = yaml.safe_load(sdl)
        gpu = parsed["profiles"]["compute"]["app"]["resources"]["gpu"]
        assert gpu["units"] == 4

    def test_env_vars_as_flat_strings(self):
        """Environment variables are flat KEY=VALUE strings."""
        env = {"MODEL": "llama-7b", "PORT": "8000", "EMPTY": ""}
        sdl = build_inference_sdl(image="vllm:latest", env=env)
        parsed = yaml.safe_load(sdl)
        envs = parsed["services"]["app"]["env"]
        assert all(isinstance(e, str) for e in envs)
        assert "MODEL=llama-7b" in envs
        assert "EMPTY=" in envs

    def test_volume_mounts_structured(self):
        """Volume definitions produce correct storage profiles and mounts."""
        volumes = [
            {"name": "shm", "mount": "/dev/shm", "size": "10Gi", "type": "ram"},
            {"name": "data", "mount": "/data", "size": "50Gi"},
        ]
        sdl = build_inference_sdl(image="vllm:latest", volumes=volumes)
        parsed = yaml.safe_load(sdl)
        storage = parsed["profiles"]["compute"]["app"]["resources"]["storage"]
        # Should have default root + 2 named volumes
        assert len(storage) == 3
        vol_names = [s.get("name") for s in storage if isinstance(s, dict) and "name" in s]
        assert "shm" in vol_names
        assert "data" in vol_names

    def test_training_sdl_sanitizes_git_repo(self):
        """Training SDL quotes user-supplied git_repo in env vars."""
        import shlex

        malicious = "https://evil.com; rm -rf /"
        sdl = build_training_sdl(
            image="pytorch:latest",
            training_script="train.py",
            git_repo=malicious,
        )
        parsed = yaml.safe_load(sdl)
        envs = parsed["services"]["training-node"]["env"]
        git_env = [e for e in envs if e.startswith("GIT_REPO=")]
        assert len(git_env) == 1
        assert shlex.quote(malicious) in git_env[0]

    def test_valid_sdl_version(self):
        """SDL always has version 2.0."""
        sdl = build_inference_sdl(image="nginx:latest")
        parsed = yaml.safe_load(sdl)
        assert parsed["version"] == "2.0"
