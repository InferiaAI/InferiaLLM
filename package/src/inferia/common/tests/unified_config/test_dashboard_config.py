"""Tests for write-dashboard-config CLI subcommand (issue #243).

Dashboard URLs are env-only after the yaml schema refactor:
  - services.api_gateway.dashboard is removed from the yaml schema.
  - write-dashboard-config reads all four URLs purely from env.
  - The --config flag is unused for this subcommand; tests verify env-only behaviour.

Covers:
  - Env vars written to config.js correctly.
  - Missing env vars → empty string (legacy fallback preserved).
  - No dashboard directory installed: exits 0 cleanly.
  - config.js format: valid JS assignment.
  - Summary line printed to stdout.
"""
import json
import os
from pathlib import Path

import pytest

from inferia import cli as cli_module
from inferia.common.unified_config.loader import _clear_cache


# ─── helpers ──────────────────────────────────────────────────────────────────

def _read_config_js(dashboard_dir: Path) -> dict:
    """Read config.js and return the parsed JSON object."""
    config_js = (dashboard_dir / "config.js").read_text()
    # Strip "window.__RUNTIME_CONFIG__ = " prefix and trailing ";"
    assert config_js.startswith("window.__RUNTIME_CONFIG__ = "), (
        f"Unexpected config.js content: {config_js!r}"
    )
    json_str = config_js[len("window.__RUNTIME_CONFIG__ = "):].rstrip(";").strip()
    return json.loads(json_str)


@pytest.fixture(autouse=True)
def clear_loader_cache():
    """Clear the loader LRU cache before every test to avoid cross-test pollution."""
    _clear_cache()
    yield
    _clear_cache()


# ─── CLI subcommand tests ──────────────────────────────────────────────────────

class TestWriteDashboardConfig:
    def test_env_values_written_to_config_js(self, tmp_path, monkeypatch):
        """DASHBOARD_* env vars → config.js contains those values."""
        monkeypatch.delenv("INFERIA_CONFIG", raising=False)
        monkeypatch.setenv("DASHBOARD_API_GATEWAY_URL", "http://gw:8000")
        monkeypatch.setenv("DASHBOARD_INFERENCE_URL", "http://inf:8001")
        monkeypatch.setenv("DASHBOARD_WEB_SOCKET_URL", "ws://gw:8000")
        monkeypatch.setenv("DASHBOARD_SIDECAR_URL", "http://side:3000")

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()

        cli_module.main(
            [
                "write-dashboard-config",
                "--dashboard-dir",
                str(dashboard_dir),
            ]
        )

        obj = _read_config_js(dashboard_dir)
        assert obj["API_GATEWAY_URL"] == "http://gw:8000"
        assert obj["INFERENCE_URL"] == "http://inf:8001"
        assert obj["WEB_SOCKET_URL"] == "ws://gw:8000"
        assert obj["SIDECAR_URL"] == "http://side:3000"

    def test_missing_env_vars_produce_empty_strings(self, tmp_path, monkeypatch):
        """No DASHBOARD_* env vars → all four fields are empty string."""
        monkeypatch.delenv("INFERIA_CONFIG", raising=False)
        for var in (
            "DASHBOARD_API_GATEWAY_URL",
            "DASHBOARD_INFERENCE_URL",
            "DASHBOARD_WEB_SOCKET_URL",
            "DASHBOARD_SIDECAR_URL",
        ):
            monkeypatch.delenv(var, raising=False)

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()

        cli_module.main(
            [
                "write-dashboard-config",
                "--dashboard-dir",
                str(dashboard_dir),
            ]
        )

        obj = _read_config_js(dashboard_dir)
        assert obj["API_GATEWAY_URL"] == ""
        assert obj["INFERENCE_URL"] == ""
        assert obj["WEB_SOCKET_URL"] == ""
        assert obj["SIDECAR_URL"] == ""

    def test_partial_env_vars(self, tmp_path, monkeypatch):
        """Only some DASHBOARD_* vars set → set ones appear, rest are empty string."""
        monkeypatch.delenv("INFERIA_CONFIG", raising=False)
        monkeypatch.setenv("DASHBOARD_API_GATEWAY_URL", "http://env-gw:8000")
        monkeypatch.delenv("DASHBOARD_INFERENCE_URL", raising=False)
        monkeypatch.delenv("DASHBOARD_WEB_SOCKET_URL", raising=False)
        monkeypatch.delenv("DASHBOARD_SIDECAR_URL", raising=False)

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()

        cli_module.main(
            [
                "write-dashboard-config",
                "--dashboard-dir",
                str(dashboard_dir),
            ]
        )

        obj = _read_config_js(dashboard_dir)
        assert obj["API_GATEWAY_URL"] == "http://env-gw:8000"
        assert obj["INFERENCE_URL"] == ""
        assert obj["WEB_SOCKET_URL"] == ""
        assert obj["SIDECAR_URL"] == ""

    def test_no_dashboard_dir_exits_zero(self, tmp_path, monkeypatch, capsys):
        """Missing dashboard directory → no error, no file written."""
        monkeypatch.delenv("INFERIA_CONFIG", raising=False)
        # Point to a dir that does NOT exist
        absent_dir = tmp_path / "no_dashboard_here"
        # Should not raise
        cli_module.main(
            [
                "write-dashboard-config",
                "--dashboard-dir",
                str(absent_dir),
            ]
        )
        # No config.js should be written
        assert not (absent_dir / "config.js").exists()

    def test_config_js_is_valid_js_assignment(self, tmp_path, monkeypatch):
        """Ensures the written file is exactly 'window.__RUNTIME_CONFIG__ = {...};'."""
        monkeypatch.delenv("INFERIA_CONFIG", raising=False)
        for var in (
            "DASHBOARD_API_GATEWAY_URL",
            "DASHBOARD_INFERENCE_URL",
            "DASHBOARD_WEB_SOCKET_URL",
            "DASHBOARD_SIDECAR_URL",
        ):
            monkeypatch.delenv(var, raising=False)

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()

        cli_module.main(
            ["write-dashboard-config", "--dashboard-dir", str(dashboard_dir)]
        )

        content = (dashboard_dir / "config.js").read_text()
        assert content.startswith("window.__RUNTIME_CONFIG__ = {")
        assert content.endswith("};")

    def test_prints_summary_line(self, tmp_path, monkeypatch, capsys):
        """Subcommand prints a one-line summary to stdout."""
        monkeypatch.delenv("INFERIA_CONFIG", raising=False)
        for var in (
            "DASHBOARD_API_GATEWAY_URL",
            "DASHBOARD_INFERENCE_URL",
            "DASHBOARD_WEB_SOCKET_URL",
            "DASHBOARD_SIDECAR_URL",
        ):
            monkeypatch.delenv(var, raising=False)

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()

        cli_module.main(
            ["write-dashboard-config", "--dashboard-dir", str(dashboard_dir)]
        )

        captured = capsys.readouterr()
        assert "[inferiallm write-dashboard-config]" in captured.out
        assert "wrote" in captured.out

    def test_config_flag_ignored_safely(self, tmp_path, monkeypatch):
        """--config flag is accepted but does not affect env-only URL resolution."""
        monkeypatch.delenv("INFERIA_CONFIG", raising=False)
        monkeypatch.setenv("DASHBOARD_API_GATEWAY_URL", "http://env-only:8000")
        for var in (
            "DASHBOARD_INFERENCE_URL",
            "DASHBOARD_WEB_SOCKET_URL",
            "DASHBOARD_SIDECAR_URL",
        ):
            monkeypatch.delenv(var, raising=False)

        # Write a yaml that would have had a dashboard block in the old schema
        # (now irrelevant — schema no longer has that block, but --config still
        # accepted for forward-compat with scripts that pass it)
        yaml_file = tmp_path / "inferia.yaml"
        yaml_file.write_text("version: 1\n", encoding="utf-8")

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()

        cli_module.main(
            [
                "write-dashboard-config",
                "--config",
                str(yaml_file),
                "--dashboard-dir",
                str(dashboard_dir),
            ]
        )

        obj = _read_config_js(dashboard_dir)
        # Value comes purely from env, not from yaml
        assert obj["API_GATEWAY_URL"] == "http://env-only:8000"
        assert obj["INFERENCE_URL"] == ""
