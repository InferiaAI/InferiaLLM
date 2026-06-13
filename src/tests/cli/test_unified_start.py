"""Tests for the unified-start CLI process specification.

Verifies that _unified_process_specs() collapses the three separate web
processes (api-gateway, inference, dashboard) into a single unified-web
process, while keeping non-web services exactly as before.
"""

import pytest


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
