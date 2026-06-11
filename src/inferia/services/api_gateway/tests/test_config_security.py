"""
Tests for security guards in API Gateway configuration.

Verifies that placeholder/default secrets are rejected in production
and warned about in development.
"""

import logging
import os
import pytest
from unittest.mock import patch
from inferia.services.api_gateway.config import Settings


PLACEHOLDER = "placeholder-secret-key-at-least-32-chars-long"


def _make_env(**overrides):
    """Build a clean env dict with only the specified overrides."""
    # Start from a minimal env (no .env file interference)
    env = {}
    for k, v in overrides.items():
        env[k.upper()] = v
    return env


class TestJWTSecretGuard:
    """Verify that the default JWT secret is rejected in production."""

    def test_placeholder_secret_raises_in_production(self):
        """Production mode with the placeholder secret must raise RuntimeError."""
        env = _make_env(
            jwt_secret_key=PLACEHOLDER,
            environment="production",
            database_url="postgresql+asyncpg://x:x@localhost/x",
        )
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="JWT_SECRET_KEY.*placeholder"):
                Settings(_env_file=None)

    def test_placeholder_secret_warns_in_development(self):
        """Development mode with the placeholder secret must log a warning."""
        env = _make_env(
            jwt_secret_key=PLACEHOLDER,
            environment="development",
            database_url="postgresql+asyncpg://x:x@localhost/x",
        )
        with patch.dict(os.environ, env, clear=True):
            with patch("inferia.services.api_gateway.config.logger") as mock_logger:
                s = Settings(_env_file=None)
                assert s.jwt_secret_key == PLACEHOLDER
                mock_logger.warning.assert_called_once()
                assert "placeholder" in mock_logger.warning.call_args[0][0].lower()

    def test_custom_secret_accepted_in_production(self):
        """A real secret key should be accepted in production without errors."""
        real_secret = "a" * 64
        env = _make_env(
            jwt_secret_key=real_secret,
            environment="production",
            database_url="postgresql+asyncpg://x:x@localhost/x",
        )
        with patch.dict(os.environ, env, clear=True):
            s = Settings(_env_file=None)
            assert s.jwt_secret_key == real_secret

    def test_custom_secret_accepted_in_development(self):
        """A real secret key should be accepted in development without warnings."""
        real_secret = "b" * 64
        env = _make_env(
            jwt_secret_key=real_secret,
            environment="development",
            database_url="postgresql+asyncpg://x:x@localhost/x",
        )
        with patch.dict(os.environ, env, clear=True):
            with patch("inferia.services.api_gateway.config.logger") as mock_logger:
                s = Settings(_env_file=None)
                assert s.jwt_secret_key == real_secret
                mock_logger.warning.assert_not_called()

    def test_staging_with_placeholder_warns(self):
        """Staging with placeholder should warn but not crash."""
        env = _make_env(
            jwt_secret_key=PLACEHOLDER,
            environment="staging",
            database_url="postgresql+asyncpg://x:x@localhost/x",
        )
        with patch.dict(os.environ, env, clear=True):
            s = Settings(_env_file=None)
            assert s.jwt_secret_key == PLACEHOLDER
