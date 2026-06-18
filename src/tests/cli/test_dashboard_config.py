"""Tests for the write-dashboard-config CLI subcommand.

Dashboard runtime config is env-only: write-dashboard-config reads all four
URLs plus AUTH_PROVIDER / EXTERNAL_AUTH_URL purely from env (DASHBOARD_*,
VITE_*, AUTH_PROVIDER, EXTERNAL_AUTH_URL). There is no yaml/config-file source.

Covers:
  - Env vars written to config.js correctly.
  - Missing env vars → same-origin defaults (/api, /inf) / empty string.
  - No dashboard directory installed: exits 0 cleanly.
  - config.js format: valid JS assignment + summary line.
  - AUTH_PROVIDER / EXTERNAL_AUTH_URL resolution (VITE_ precedence).
"""
import json
from pathlib import Path

import cli as cli_module


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


# ─── CLI subcommand tests ──────────────────────────────────────────────────────

class TestWriteDashboardConfig:
    def test_env_values_written_to_config_js(self, tmp_path, monkeypatch):
        """DASHBOARD_* env vars → config.js contains those values."""
        monkeypatch.setenv("DASHBOARD_API_GATEWAY_URL", "http://gw:8000")
        monkeypatch.setenv("DASHBOARD_INFERENCE_URL", "http://inf:8001")
        monkeypatch.setenv("DASHBOARD_WEB_SOCKET_URL", "ws://gw:8000")
        monkeypatch.setenv("DASHBOARD_SIDECAR_URL", "http://side:3000")

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()

        cli_module.main(
            ["write-dashboard-config", "--dashboard-dir", str(dashboard_dir)]
        )

        obj = _read_config_js(dashboard_dir)
        assert obj["API_GATEWAY_URL"] == "http://gw:8000"
        assert obj["INFERENCE_URL"] == "http://inf:8001"
        assert obj["WEB_SOCKET_URL"] == "ws://gw:8000"
        assert obj["SIDECAR_URL"] == "http://side:3000"

    def test_missing_env_vars_produce_same_origin_defaults(self, tmp_path, monkeypatch):
        """No DASHBOARD_* env vars → API_GATEWAY_URL defaults to /api, INFERENCE_URL to /inf."""
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

        obj = _read_config_js(dashboard_dir)
        assert obj["API_GATEWAY_URL"] == "/api"
        assert obj["INFERENCE_URL"] == "/inf"
        assert obj["WEB_SOCKET_URL"] == ""
        assert obj["SIDECAR_URL"] == ""

    def test_auth_provider_from_vite_env(self, tmp_path, monkeypatch):
        """VITE_AUTH_PROVIDER → AUTH_PROVIDER in the runtime config (so the SPA
        can enter SSO mode without rebaking the build)."""
        monkeypatch.setenv("VITE_AUTH_PROVIDER", "inferiaauth")
        monkeypatch.delenv("AUTH_PROVIDER", raising=False)

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()
        cli_module.main(["write-dashboard-config", "--dashboard-dir", str(dashboard_dir)])

        assert _read_config_js(dashboard_dir)["AUTH_PROVIDER"] == "inferiaauth"

    def test_auth_provider_falls_back_to_auth_provider_env(self, tmp_path, monkeypatch):
        """When VITE_AUTH_PROVIDER is unset, AUTH_PROVIDER env is used."""
        monkeypatch.delenv("VITE_AUTH_PROVIDER", raising=False)
        monkeypatch.setenv("AUTH_PROVIDER", "oidc")

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()
        cli_module.main(["write-dashboard-config", "--dashboard-dir", str(dashboard_dir)])

        assert _read_config_js(dashboard_dir)["AUTH_PROVIDER"] == "oidc"

    def test_auth_provider_empty_when_unset(self, tmp_path, monkeypatch):
        """Neither env set → AUTH_PROVIDER is empty (SPA defaults to 'local')."""
        monkeypatch.delenv("VITE_AUTH_PROVIDER", raising=False)
        monkeypatch.delenv("AUTH_PROVIDER", raising=False)

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()
        cli_module.main(["write-dashboard-config", "--dashboard-dir", str(dashboard_dir)])

        assert _read_config_js(dashboard_dir)["AUTH_PROVIDER"] == ""

    def test_partial_env_vars(self, tmp_path, monkeypatch):
        """Only some DASHBOARD_* vars set → set ones appear, rest are empty / default."""
        monkeypatch.setenv("DASHBOARD_API_GATEWAY_URL", "http://env-gw:8000")
        for var in (
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

        obj = _read_config_js(dashboard_dir)
        assert obj["API_GATEWAY_URL"] == "http://env-gw:8000"
        assert obj["INFERENCE_URL"] == "/inf"
        assert obj["WEB_SOCKET_URL"] == ""
        assert obj["SIDECAR_URL"] == ""

    def test_no_dashboard_dir_exits_zero(self, tmp_path, monkeypatch):
        """Missing dashboard directory → no error, no file written."""
        absent_dir = tmp_path / "no_dashboard_here"
        # Should not raise
        cli_module.main(
            ["write-dashboard-config", "--dashboard-dir", str(absent_dir)]
        )
        assert not (absent_dir / "config.js").exists()

    def test_config_js_is_valid_js_assignment(self, tmp_path, monkeypatch):
        """Ensures the written file is exactly 'window.__RUNTIME_CONFIG__ = {...};'."""
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

    # ─── EXTERNAL_AUTH_URL tests ──────────────────────────────────────────────

    def test_external_auth_url_from_vite_env(self, tmp_path, monkeypatch):
        """VITE_EXTERNAL_AUTH_URL → EXTERNAL_AUTH_URL in the runtime config."""
        monkeypatch.setenv("VITE_EXTERNAL_AUTH_URL", "https://auth.local:3005")
        monkeypatch.delenv("EXTERNAL_AUTH_URL", raising=False)

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()
        cli_module.main(["write-dashboard-config", "--dashboard-dir", str(dashboard_dir)])

        assert _read_config_js(dashboard_dir)["EXTERNAL_AUTH_URL"] == "https://auth.local:3005"

    def test_external_auth_url_falls_back_to_plain_env(self, tmp_path, monkeypatch):
        """When VITE_EXTERNAL_AUTH_URL is unset, EXTERNAL_AUTH_URL env is used."""
        monkeypatch.delenv("VITE_EXTERNAL_AUTH_URL", raising=False)
        monkeypatch.setenv("EXTERNAL_AUTH_URL", "https://plain-auth.local")

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()
        cli_module.main(["write-dashboard-config", "--dashboard-dir", str(dashboard_dir)])

        assert _read_config_js(dashboard_dir)["EXTERNAL_AUTH_URL"] == "https://plain-auth.local"

    def test_external_auth_url_empty_when_unset(self, tmp_path, monkeypatch):
        """Neither env set → EXTERNAL_AUTH_URL is empty string."""
        monkeypatch.delenv("VITE_EXTERNAL_AUTH_URL", raising=False)
        monkeypatch.delenv("EXTERNAL_AUTH_URL", raising=False)

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()
        cli_module.main(["write-dashboard-config", "--dashboard-dir", str(dashboard_dir)])

        assert _read_config_js(dashboard_dir)["EXTERNAL_AUTH_URL"] == ""

    def test_vite_external_auth_url_takes_precedence(self, tmp_path, monkeypatch):
        """VITE_EXTERNAL_AUTH_URL wins over EXTERNAL_AUTH_URL when both are set."""
        monkeypatch.setenv("VITE_EXTERNAL_AUTH_URL", "https://vite-auth.local")
        monkeypatch.setenv("EXTERNAL_AUTH_URL", "https://plain-auth.local")

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()
        cli_module.main(["write-dashboard-config", "--dashboard-dir", str(dashboard_dir)])

        assert _read_config_js(dashboard_dir)["EXTERNAL_AUTH_URL"] == "https://vite-auth.local"

    def test_external_auth_url_coexists_with_auth_provider(self, tmp_path, monkeypatch):
        """Both AUTH_PROVIDER and EXTERNAL_AUTH_URL are written to the same config."""
        monkeypatch.setenv("VITE_AUTH_PROVIDER", "inferiaauth")
        monkeypatch.setenv("VITE_EXTERNAL_AUTH_URL", "https://auth.local")
        monkeypatch.delenv("AUTH_PROVIDER", raising=False)
        monkeypatch.delenv("EXTERNAL_AUTH_URL", raising=False)

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()
        cli_module.main(["write-dashboard-config", "--dashboard-dir", str(dashboard_dir)])

        obj = _read_config_js(dashboard_dir)
        assert obj["AUTH_PROVIDER"] == "inferiaauth"
        assert obj["EXTERNAL_AUTH_URL"] == "https://auth.local"

    def test_explicit_relative_values_pass_through(self, tmp_path, monkeypatch):
        """Explicit relative env values /api and /inf pass through unchanged."""
        monkeypatch.setenv("DASHBOARD_API_GATEWAY_URL", "/api")
        monkeypatch.setenv("DASHBOARD_INFERENCE_URL", "/inf")
        monkeypatch.delenv("DASHBOARD_WEB_SOCKET_URL", raising=False)
        monkeypatch.delenv("DASHBOARD_SIDECAR_URL", raising=False)

        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()
        cli_module.main(["write-dashboard-config", "--dashboard-dir", str(dashboard_dir)])

        obj = _read_config_js(dashboard_dir)
        assert obj["API_GATEWAY_URL"] == "/api"
        assert obj["INFERENCE_URL"] == "/inf"
