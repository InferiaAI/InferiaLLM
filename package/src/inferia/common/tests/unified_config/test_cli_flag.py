"""Tests that the CLI's --config flag plumbs INFERIA_CONFIG into the process env
before any service Settings is constructed."""
import os
import pytest

from inferia import cli as cli_module


@pytest.fixture
def stub_runners(monkeypatch):
    """Stub out the actual service runners so main() doesn't try to start anything."""
    seen: dict[str, str | None] = {"INFERIA_CONFIG": None}

    def _record(*args, **kwargs):
        seen["INFERIA_CONFIG"] = os.environ.get("INFERIA_CONFIG")

    for name in (
        "run_all",
        "run_api_gateway_service",
        "run_inference_service",
        "run_guardrail_service",
        "run_data_service",
        "run_orchestration_stack",
        "run_skypilot_server",
        "run_init",
        "run_migrate",
    ):
        if hasattr(cli_module, name):
            monkeypatch.setattr(cli_module, name, _record)
    return seen


def test_config_flag_sets_env(stub_runners, monkeypatch):
    monkeypatch.delenv("INFERIA_CONFIG", raising=False)
    cli_module.main(["start", "api-gateway", "--config", "/tmp/inferia.yaml"])
    assert stub_runners["INFERIA_CONFIG"] == "/tmp/inferia.yaml"


def test_no_config_flag_leaves_env_alone(stub_runners, monkeypatch):
    monkeypatch.delenv("INFERIA_CONFIG", raising=False)
    cli_module.main(["start", "api-gateway"])
    assert stub_runners["INFERIA_CONFIG"] is None


def test_config_flag_with_metacharacters_passes_through(stub_runners, monkeypatch):
    """Path is taken as a literal — no shell eval."""
    monkeypatch.delenv("INFERIA_CONFIG", raising=False)
    cli_module.main(["start", "api-gateway", "--config", "/tmp/a;rm -rf b"])
    assert stub_runners["INFERIA_CONFIG"] == "/tmp/a;rm -rf b"


def test_config_flag_overrides_pre_existing_env(stub_runners, monkeypatch):
    monkeypatch.setenv("INFERIA_CONFIG", "/old/path.yaml")
    cli_module.main(["start", "api-gateway", "--config", "/new/path.yaml"])
    assert stub_runners["INFERIA_CONFIG"] == "/new/path.yaml"
