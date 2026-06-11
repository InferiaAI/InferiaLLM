"""Tests for orchestration service config properties."""

import os
from pathlib import Path
from unittest.mock import patch

from inferia.services.orchestration.config import Settings

# Resolve the config source file relative to this test file
_CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.py"


def _make_settings(**overrides):
    """Create Settings isolated from any .env file or environment variables."""
    env = {
        "ENVIRONMENT": overrides.get("environment", "development"),
    }
    with patch.dict(os.environ, env, clear=False):
        return Settings(
            _env_file=None,
            **overrides,
        )


def test_is_development_returns_true_for_development_env():
    """is_development should return True when environment is 'development'."""
    s = _make_settings(environment="development")
    assert s.is_development is True


def test_is_development_returns_false_for_production_env():
    """is_development should return False when environment is 'production'."""
    s = _make_settings(environment="production")
    assert s.is_development is False


def test_is_development_defined_once():
    """is_development property must be defined exactly once in Settings source."""
    source = _CONFIG_FILE.read_text()
    count = source.count("def is_development")
    assert count == 1, (
        f"Expected is_development to be defined once, but found {count} definitions"
    )
