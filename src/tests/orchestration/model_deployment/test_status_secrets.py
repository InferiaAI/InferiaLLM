"""Tests for _without_secrets — credentials must not leave /status.

External provider deployments keep their API key in `configuration`, which
the deployment status endpoint returns to the dashboard.
"""
from __future__ import annotations

from orchestration.models.model_deployment.deployment_server import _without_secrets


def test_drops_api_key():
    config = {
        "model": "gpt-4o",
        "api_key": "sk-live-secret",
        "provider": "openai",
        "workload_type": "external",
    }
    assert _without_secrets(config) == {
        "model": "gpt-4o",
        "provider": "openai",
        "workload_type": "external",
    }


def test_drops_every_secret_key():
    config = {
        "api_key": "a",
        "credentials": "b",
        "token": "c",
        "secret": "d",
        "password": "e",
        "keep": "f",
    }
    assert _without_secrets(config) == {"keep": "f"}


def test_matches_regardless_of_case():
    assert _without_secrets({"API_KEY": "a", "Token": "b", "keep": "c"}) == {"keep": "c"}


def test_keeps_what_the_dashboard_reads():
    # DeploymentDetail.tsx and Sandbox.tsx read these four.
    config = {
        "workload_type": "training",
        "git_repo": "https://example.invalid/repo.git",
        "training_script": "train.py",
        "dataset_url": "s3://bucket/data",
        "api_key": "sk-live-secret",
    }
    assert _without_secrets(config) == {
        "workload_type": "training",
        "git_repo": "https://example.invalid/repo.git",
        "training_script": "train.py",
        "dataset_url": "s3://bucket/data",
    }


def test_empty_config():
    assert _without_secrets({}) == {}
