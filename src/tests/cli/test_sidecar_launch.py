"""run_nosana_sidecar must hand the DePIN sidecar subprocess the env it needs to
reach (and bind) the right ports when the internal ports are remapped for host
networking — ORCHESTRATOR_URL (its callback to the orchestration control plane,
derived from HTTP_PORT) and DEPIN_SIDECAR_PORT (its own bind). Otherwise the
sidecar's watchdog/job-monitoring dials a stale :8080 and its bind diverges from
the URL the control plane dials.
"""
import cli


def _run_and_capture(monkeypatch, env_overrides):
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)
    # An operator hasn't pinned the sidecar/orchestration URLs explicitly.
    for v in ("ORCHESTRATOR_URL", "ORCHESTRATION_URL", "API_GATEWAY_URL"):
        monkeypatch.delenv(v, raising=False)
    # Pretend the sidecar dir + node_modules already exist → skip npm install.
    monkeypatch.setattr("os.path.isdir", lambda _p: True)

    captured = {}

    class _FakePopen:
        def __init__(self, cmd, cwd=None, env=None):
            captured["cmd"], captured["cwd"], captured["env"] = cmd, cwd, env

    monkeypatch.setattr(cli.subprocess, "Popen", _FakePopen)
    cli.run_nosana_sidecar(env="production")
    return captured


def test_sidecar_env_derives_from_port_vars(monkeypatch):
    cap = _run_and_capture(monkeypatch, {
        "HTTP_PORT": "18080", "DEPIN_SIDECAR_PORT": "3055", "APP_PORT": "8000",
    })
    env = cap["env"]
    # callback to orchestration follows HTTP_PORT...
    assert env["ORCHESTRATOR_URL"] == "http://localhost:18080"
    # ...and the sidecar's own bind port is passed through so it matches the URL
    # the control plane derives (also from DEPIN_SIDECAR_PORT).
    assert env["DEPIN_SIDECAR_PORT"] == "3055"
    # gateway base for credential polling stays on the unified /api mount.
    assert env["API_GATEWAY_URL"].endswith("/api")


def test_sidecar_env_defaults_when_ports_unset(monkeypatch):
    for v in ("HTTP_PORT", "DEPIN_SIDECAR_PORT"):
        monkeypatch.delenv(v, raising=False)
    cap = _run_and_capture(monkeypatch, {"APP_PORT": "8000"})
    assert cap["env"]["ORCHESTRATOR_URL"] == "http://localhost:8080"
    assert cap["env"]["DEPIN_SIDECAR_PORT"] == "3000"


def test_sidecar_env_explicit_orchestrator_url_wins(monkeypatch):
    cap = _run_and_capture(monkeypatch, {"HTTP_PORT": "18080", "APP_PORT": "8000"})
    # (ORCHESTRATOR_URL was delenv'd in the helper) now set it explicitly and
    # confirm setdefault preserves it.
    # Re-run with an explicit value:
    import os
    os.environ["ORCHESTRATOR_URL"] = "http://orch.remote:9000"
    try:
        monkeypatch.setattr("os.path.isdir", lambda _p: True)
        captured = {}

        class _FakePopen:
            def __init__(self, cmd, cwd=None, env=None):
                captured["env"] = env

        monkeypatch.setattr(cli.subprocess, "Popen", _FakePopen)
        cli.run_nosana_sidecar(env="production")
        assert captured["env"]["ORCHESTRATOR_URL"] == "http://orch.remote:9000"
    finally:
        os.environ.pop("ORCHESTRATOR_URL", None)
