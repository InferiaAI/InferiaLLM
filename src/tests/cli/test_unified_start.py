"""Tests for the unified-start CLI process specification.

Verifies that _unified_process_specs() collapses the three separate web
processes (api-gateway, inference, dashboard) into a single unified-web
process, while keeping non-web services exactly as before.
"""



def test_unified_process_specs_collapses_web_into_one():
    from cli import _unified_process_specs

    specs = _unified_process_specs()
    names = {n for n, _ in specs}

    # The unified web process must be present
    assert "unified-web" in names

    # The three separate web processes must be gone
    old_web = {"api-gateway", "inference", "dashboard"}
    overlap = old_web & names
    assert old_web.isdisjoint(names), (
        f"Expected old web processes removed, but found: {overlap}"
    )

    # Non-web services stay separate
    assert "orchestration-api" in names


def test_unified_web_target_is_run_unified_web():
    from cli import _unified_process_specs, run_unified_web

    specs = dict(_unified_process_specs())
    assert specs["unified-web"] is run_unified_web


def test_unified_process_specs_preserves_non_web_services():
    """All four non-web services must be present and unchanged."""
    from cli import _unified_process_specs, run_orchestration_service, run_worker, run_nosana_sidecar, run_skypilot_server

    specs = dict(_unified_process_specs())

    assert specs.get("orchestration-api") is run_orchestration_service
    assert specs.get("orchestration-worker") is run_worker
    assert specs.get("nosana-sidecar") is run_nosana_sidecar
    assert specs.get("skypilot-api") is run_skypilot_server


def test_unified_process_specs_total_count():
    """Should have exactly 5 processes: 1 web + 4 non-web."""
    from cli import _unified_process_specs

    specs = _unified_process_specs()
    assert len(specs) == 5, f"Expected 5 processes, got {len(specs)}: {[n for n,_ in specs]}"


def test_unified_process_specs_no_duplicate_names():
    """Process names must be unique."""
    from cli import _unified_process_specs

    specs = _unified_process_specs()
    names = [n for n, _ in specs]
    assert len(names) == len(set(names)), f"Duplicate process names found: {names}"


def test_run_unified_web_is_callable():
    """run_unified_web must be importable and callable."""
    from cli import run_unified_web

    assert callable(run_unified_web)


def test_unified_process_specs_returns_list_of_tuples():
    """Return type must be a list of (str, callable) tuples."""
    from cli import _unified_process_specs

    specs = _unified_process_specs()
    assert isinstance(specs, list), "Expected a list"
    for item in specs:
        assert isinstance(item, tuple) and len(item) == 2, f"Expected (name, target) tuple, got: {item}"
        name, target = item
        assert isinstance(name, str), f"Name must be a string, got: {type(name)}"
        assert callable(target), f"Target must be callable, got: {type(target)}"


def test_loopback_env_points_services_at_mount_prefixes(monkeypatch):
    """In unified mode the co-located services must call each other via the
    mount prefixes (/api, /inf) on the loopback, or bare /internal POSTs fall
    through to the SPA catch-all and 405."""
    from cli import _set_unified_loopback_env

    monkeypatch.delenv("API_GATEWAY_URL", raising=False)
    monkeypatch.delenv("INFERENCE_URL", raising=False)

    _set_unified_loopback_env(8000)

    import os
    assert os.environ["API_GATEWAY_URL"] == "http://localhost:8000/api"
    assert os.environ["INFERENCE_URL"] == "http://localhost:8000/inf"


def test_loopback_env_honors_app_port(monkeypatch):
    from cli import _set_unified_loopback_env

    monkeypatch.delenv("API_GATEWAY_URL", raising=False)
    monkeypatch.delenv("INFERENCE_URL", raising=False)

    _set_unified_loopback_env(9100)

    import os
    assert os.environ["API_GATEWAY_URL"] == "http://localhost:9100/api"
    assert os.environ["INFERENCE_URL"] == "http://localhost:9100/inf"


def test_loopback_env_does_not_override_explicit(monkeypatch):
    """setdefault: an explicit env (e.g. split mode) must win."""
    from cli import _set_unified_loopback_env

    monkeypatch.setenv("API_GATEWAY_URL", "http://gw.internal:8000")
    monkeypatch.setenv("INFERENCE_URL", "http://inf.internal:8001")

    _set_unified_loopback_env(8000)

    import os
    assert os.environ["API_GATEWAY_URL"] == "http://gw.internal:8000"
    assert os.environ["INFERENCE_URL"] == "http://inf.internal:8001"


# --- DePIN sidecar gateway URL (the Nosana "Service not initialized" fix) ---
# The sidecar runs as its own process and must derive the /api mount itself;
# without /api its config poll hits the SPA catch-all and no DePIN credentials
# load (nosana "disabled" -> deploy 503).


def test_sidecar_gateway_url_defaults_to_api_mount():
    from cli import _sidecar_api_gateway_url

    assert _sidecar_api_gateway_url({}) == "http://localhost:8000/api"


def test_sidecar_gateway_url_honors_app_port():
    from cli import _sidecar_api_gateway_url

    assert _sidecar_api_gateway_url({"APP_PORT": "9100"}) == "http://localhost:9100/api"


def test_sidecar_gateway_url_preserves_explicit():
    """Split-mode operators set API_GATEWAY_URL to the gateway's own host."""
    from cli import _sidecar_api_gateway_url

    assert (
        _sidecar_api_gateway_url({"API_GATEWAY_URL": "http://gw.internal:8000"})
        == "http://gw.internal:8000"
    )


def test_sidecar_gateway_url_never_bare_localhost_without_api():
    """Regression: the old fallback was bare http://localhost:8000 (no /api),
    which made the sidecar poll the SPA catch-all and load zero credentials."""
    from cli import _sidecar_api_gateway_url

    url = _sidecar_api_gateway_url({})
    assert url.endswith("/api"), f"sidecar gateway URL must include /api mount, got {url!r}"
