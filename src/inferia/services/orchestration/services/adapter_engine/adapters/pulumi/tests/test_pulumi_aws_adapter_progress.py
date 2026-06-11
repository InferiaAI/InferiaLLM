"""Tests for the PulumiAWSAdapter module-level helpers that survived T10.

Pre-T10 (May 2026), this file also verified provision_node's 8-phase
progress-event sequence. The reconciler (T15+) emits those events now,
not the adapter, so those tests were removed. What remains is testing
for the pure helpers that still live in pulumi_aws_adapter:

  * _resolve_instance_type — GPU-name -> EC2-instance-type defensive mapping
  * _validate_control_plane_url — reject hostnames an EC2 worker can't reach
  * _resolve_control_plane_url — precedence between settings and the
    cloudflared sidecar file

The phase-event coverage now lives in the reconciler / handler tests.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# GPU-name → EC2 instance-type mapping (defensive layer)
# ---------------------------------------------------------------------------

def test_resolve_instance_type_passthrough_for_real_instance():
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        _resolve_instance_type,
    )
    inst, mapped = _resolve_instance_type("g4dn.xlarge")
    assert inst == "g4dn.xlarge"
    assert mapped is None


def test_resolve_instance_type_maps_t4():
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        _resolve_instance_type,
    )
    inst, mapped = _resolve_instance_type("T4")
    assert inst == "g4dn.xlarge"
    assert mapped == "T4"


def test_resolve_instance_type_unknown_gpu_passes_through():
    """Unknown values pass through unchanged — Pulumi will surface the
    AWS error via pulumi_up/failed, which the new UX captures."""
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        _resolve_instance_type,
    )
    inst, mapped = _resolve_instance_type("MADEUP_GPU")
    assert inst == "MADEUP_GPU"
    assert mapped is None


def test_resolve_instance_type_is_case_insensitive():
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        _resolve_instance_type,
    )
    inst, mapped = _resolve_instance_type("t4")
    assert inst == "g4dn.xlarge"
    assert mapped == "t4"


# ---------------------------------------------------------------------------
# Control-plane URL resolution and validation
# ---------------------------------------------------------------------------

def test_validate_control_plane_url_accepts_https():
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        _validate_control_plane_url,
    )
    assert _validate_control_plane_url("https://example.trycloudflare.com") is None
    assert _validate_control_plane_url("http://example.com:8000") is None


def test_validate_control_plane_url_rejects_empty():
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        _validate_control_plane_url,
    )
    err = _validate_control_plane_url("")
    assert err is not None
    assert "not configured" in err
    err = _validate_control_plane_url(None)
    assert err is not None


def test_validate_control_plane_url_rejects_localhost():
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        _validate_control_plane_url,
    )
    for bad in ("http://localhost:8000", "http://127.0.0.1:8000",
                "http://0.0.0.0:8000", "http://inferia-app:8000/"):
        err = _validate_control_plane_url(bad)
        assert err is not None, f"expected reject for {bad}"
        assert "not reachable" in err


def test_validate_control_plane_url_rejects_missing_scheme():
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        _validate_control_plane_url,
    )
    err = _validate_control_plane_url("example.trycloudflare.com")
    assert err is not None
    assert "scheme" in err


def test_resolve_control_plane_url_prefers_settings(monkeypatch, tmp_path):
    """When settings.control_plane_external_url is set, it wins over the
    sidecar file."""
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi import (
        pulumi_aws_adapter as mod,
    )
    monkeypatch.setattr(mod.settings, "control_plane_external_url", "https://from-settings.example")
    tunnel_file = tmp_path / "url"
    tunnel_file.write_text("https://from-file.example")
    monkeypatch.setattr(mod, "_TUNNEL_URL_FILE", str(tunnel_file))
    assert mod._resolve_control_plane_url() == "https://from-settings.example"


def test_resolve_control_plane_url_falls_back_to_sidecar_file(monkeypatch, tmp_path):
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi import (
        pulumi_aws_adapter as mod,
    )
    monkeypatch.setattr(mod.settings, "control_plane_external_url", "")
    tunnel_file = tmp_path / "url"
    tunnel_file.write_text("https://from-cloudflared.trycloudflare.com\n")
    monkeypatch.setattr(mod, "_TUNNEL_URL_FILE", str(tunnel_file))
    assert mod._resolve_control_plane_url() == "https://from-cloudflared.trycloudflare.com"


def test_resolve_control_plane_url_returns_none_when_nothing_set(monkeypatch, tmp_path):
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi import (
        pulumi_aws_adapter as mod,
    )
    monkeypatch.setattr(mod.settings, "control_plane_external_url", "")
    monkeypatch.setattr(mod, "_TUNNEL_URL_FILE", str(tmp_path / "does-not-exist"))
    assert mod._resolve_control_plane_url() is None


def test_validate_control_plane_url_rejects_unqualified_hostname():
    """Docker-compose service names like 'api-gateway' or 'inferia-app'
    can never be resolved by an EC2 instance — reject them even when
    they're not in the explicit _UNREACHABLE_HOSTS list."""
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.pulumi_aws_adapter import (
        _validate_control_plane_url,
    )
    for bad in ("http://api-gateway:8000", "http://my-service:9000",
                "https://random-hostname"):
        err = _validate_control_plane_url(bad)
        assert err is not None, f"expected reject for {bad}"
        assert "unqualified hostname" in err


def test_resolve_control_plane_url_falls_through_when_settings_invalid(monkeypatch, tmp_path):
    """When settings.control_plane_external_url is at its docker-only
    default (`http://api-gateway:8000`), the resolver must fall through
    to the cloudflared sidecar file instead of returning the bad URL."""
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi import (
        pulumi_aws_adapter as mod,
    )
    monkeypatch.setattr(mod.settings, "control_plane_external_url",
                        "http://api-gateway:8000")
    tunnel_file = tmp_path / "url"
    tunnel_file.write_text("https://abc-def.trycloudflare.com")
    monkeypatch.setattr(mod, "_TUNNEL_URL_FILE", str(tunnel_file))
    assert mod._resolve_control_plane_url() == "https://abc-def.trycloudflare.com"
