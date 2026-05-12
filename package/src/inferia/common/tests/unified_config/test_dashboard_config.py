"""Tests for the dashboard sub-section in unified_config (Phase 2 PR D, issue #243).

Covers:
  - Schema-level: DashboardSection reads and rejects unknown fields.
  - write-dashboard-config: CLI subcommand writes config.js correctly.
  - Legacy fallback: yaml nulls + env DASHBOARD_* vars.
  - No yaml at all: all fields empty string.
  - No dashboard directory installed: exits 0 cleanly.
"""
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from inferia.common.unified_config.schema import InferiaConfig, DashboardSection
from inferia import cli as cli_module
from inferia.common.unified_config.loader import _clear_cache


# ─── helpers ──────────────────────────────────────────────────────────────────

def _base_dict(**overrides):
    """Minimum valid InferiaConfig input."""
    base = {"version": 1, "environment": "development", "log_level": "INFO"}
    base.update(overrides)
    return base


def _write_yaml(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# ─── Schema-level tests ────────────────────────────────────────────────────────

class TestDashboardSectionSchema:
    def test_defaults_are_all_none(self):
        d = DashboardSection()
        assert d.api_gateway_url is None
        assert d.inference_url is None
        assert d.web_socket_url is None
        assert d.sidecar_url is None

    def test_all_fields_set(self):
        d = DashboardSection(
            api_gateway_url="http://gw:8000",
            inference_url="http://inf:8001",
            web_socket_url="ws://gw:8000",
            sidecar_url="http://side:3000",
        )
        assert d.api_gateway_url == "http://gw:8000"
        assert d.inference_url == "http://inf:8001"
        assert d.web_socket_url == "ws://gw:8000"
        assert d.sidecar_url == "http://side:3000"

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError, match="extra_unknown_field"):
            DashboardSection(extra_unknown_field="bad")

    def test_nested_in_api_gateway(self):
        cfg = InferiaConfig.model_validate(
            _base_dict(
                services={
                    "api_gateway": {
                        "dashboard": {
                            "api_gateway_url": "http://gw:8000",
                            "inference_url": "http://inf:8001",
                            "web_socket_url": "ws://gw:8000",
                            "sidecar_url": "http://side:3000",
                        }
                    }
                }
            )
        )
        d = cfg.services.api_gateway.dashboard
        assert d.api_gateway_url == "http://gw:8000"
        assert d.inference_url == "http://inf:8001"
        assert d.web_socket_url == "ws://gw:8000"
        assert d.sidecar_url == "http://side:3000"

    def test_unknown_field_inside_api_gateway_dashboard_fails(self):
        with pytest.raises(ValidationError, match="bogus_key"):
            InferiaConfig.model_validate(
                _base_dict(
                    services={
                        "api_gateway": {
                            "dashboard": {"bogus_key": "oops"}
                        }
                    }
                )
            )

    def test_api_gateway_defaults_have_dashboard_section(self):
        cfg = InferiaConfig.model_validate(_base_dict())
        assert isinstance(cfg.services.api_gateway.dashboard, DashboardSection)
        assert cfg.services.api_gateway.dashboard.api_gateway_url is None

    def test_partial_dashboard_fields_allowed(self):
        cfg = InferiaConfig.model_validate(
            _base_dict(
                services={
                    "api_gateway": {
                        "dashboard": {"api_gateway_url": "http://gw:8000"}
                    }
                }
            )
        )
        d = cfg.services.api_gateway.dashboard
        assert d.api_gateway_url == "http://gw:8000"
        assert d.inference_url is None
        assert d.web_socket_url is None
        assert d.sidecar_url is None

    def test_null_fields_pass_schema(self):
        cfg = InferiaConfig.model_validate(
            _base_dict(
                services={
                    "api_gateway": {
                        "dashboard": {
                            "api_gateway_url": None,
                            "inference_url": None,
                            "web_socket_url": None,
                            "sidecar_url": None,
                        }
                    }
                }
            )
        )
        d = cfg.services.api_gateway.dashboard
        assert d.api_gateway_url is None


# ─── CLI subcommand tests ──────────────────────────────────────────────────────

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


class TestWriteDashboardConfig:
    def test_yaml_values_written_to_config_js(self, tmp_path, monkeypatch):
        """Yaml dashboard block → config.js contains those values."""
        monkeypatch.delenv("INFERIA_CONFIG", raising=False)
        # Remove any legacy DASHBOARD_* env vars that might leak
        for var in (
            "DASHBOARD_API_GATEWAY_URL",
            "DASHBOARD_INFERENCE_URL",
            "DASHBOARD_WEB_SOCKET_URL",
            "DASHBOARD_SIDECAR_URL",
        ):
            monkeypatch.delenv(var, raising=False)

        yaml_file = _write_yaml(
            tmp_path / "inferia.yaml",
            """\
version: 1
services:
  api_gateway:
    dashboard:
      api_gateway_url: http://gw:8000
      inference_url: http://inf:8001
      web_socket_url: ws://gw:8000
      sidecar_url: http://side:3000
""",
        )
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
        assert obj["API_GATEWAY_URL"] == "http://gw:8000"
        assert obj["INFERENCE_URL"] == "http://inf:8001"
        assert obj["WEB_SOCKET_URL"] == "ws://gw:8000"
        assert obj["SIDECAR_URL"] == "http://side:3000"

    def test_legacy_fallback_when_yaml_fields_are_null(self, tmp_path, monkeypatch):
        """Yaml leaves all dashboard fields null → fall back to DASHBOARD_* env vars."""
        monkeypatch.setenv("DASHBOARD_API_GATEWAY_URL", "http://env-gw:8000")
        monkeypatch.delenv("DASHBOARD_INFERENCE_URL", raising=False)
        monkeypatch.delenv("DASHBOARD_WEB_SOCKET_URL", raising=False)
        monkeypatch.delenv("DASHBOARD_SIDECAR_URL", raising=False)

        yaml_file = _write_yaml(
            tmp_path / "inferia.yaml",
            """\
version: 1
services:
  api_gateway:
    dashboard:
      api_gateway_url:
      inference_url:
      web_socket_url:
      sidecar_url:
""",
        )
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
        assert obj["API_GATEWAY_URL"] == "http://env-gw:8000"
        assert obj["INFERENCE_URL"] == ""
        assert obj["WEB_SOCKET_URL"] == ""
        assert obj["SIDECAR_URL"] == ""

    def test_no_yaml_all_empty_strings(self, tmp_path, monkeypatch):
        """No yaml file, no DASHBOARD_* env vars → all four fields are empty string."""
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

    def test_yaml_env_interpolation_for_dashboard_urls(self, tmp_path, monkeypatch):
        """${VAR:-} in yaml is expanded via env before the value is used."""
        monkeypatch.setenv("DASHBOARD_API_GATEWAY_URL", "http://from-env:8000")
        monkeypatch.delenv("DASHBOARD_INFERENCE_URL", raising=False)
        monkeypatch.delenv("DASHBOARD_WEB_SOCKET_URL", raising=False)
        monkeypatch.delenv("DASHBOARD_SIDECAR_URL", raising=False)

        yaml_file = _write_yaml(
            tmp_path / "inferia.yaml",
            """\
version: 1
services:
  api_gateway:
    dashboard:
      api_gateway_url: ${DASHBOARD_API_GATEWAY_URL:-}
      inference_url: ${DASHBOARD_INFERENCE_URL:-}
      web_socket_url: ${DASHBOARD_WEB_SOCKET_URL:-}
      sidecar_url: ${DASHBOARD_SIDECAR_URL:-}
""",
        )
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
        assert obj["API_GATEWAY_URL"] == "http://from-env:8000"
        assert obj["INFERENCE_URL"] == ""

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

    def test_yaml_wins_over_env_when_not_null(self, tmp_path, monkeypatch):
        """Explicit yaml value takes precedence over DASHBOARD_* env var."""
        monkeypatch.setenv("DASHBOARD_API_GATEWAY_URL", "http://env-gw:9999")
        monkeypatch.delenv("DASHBOARD_INFERENCE_URL", raising=False)
        monkeypatch.delenv("DASHBOARD_WEB_SOCKET_URL", raising=False)
        monkeypatch.delenv("DASHBOARD_SIDECAR_URL", raising=False)

        yaml_file = _write_yaml(
            tmp_path / "inferia.yaml",
            """\
version: 1
services:
  api_gateway:
    dashboard:
      api_gateway_url: http://yaml-gw:8000
""",
        )
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
        # yaml value wins; env value is NOT used for api_gateway_url
        assert obj["API_GATEWAY_URL"] == "http://yaml-gw:8000"
