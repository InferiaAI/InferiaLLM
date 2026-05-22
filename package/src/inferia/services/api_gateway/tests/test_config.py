"""Tests for api_gateway.config — external auth validator and new fields.

Per plan C1: when AUTH_PROVIDER=external is set, four downstream OAuth/external
fields must all be present (EXTERNAL_AUTH_URL, EXTERNAL_AUTH_ISSUER,
OAUTH_CLIENT_ID, OAUTH_REDIRECT_URI). Local mode should leave them all optional.
"""

import os
from typing import Iterator
import pytest

from inferia.services.api_gateway.config import Settings


def _clean_env(monkeypatch, **overrides) -> None:
    """Strip all relevant env vars then apply overrides."""
    for key in (
        "AUTH_PROVIDER",
        "EXTERNAL_AUTH_URL",
        "EXTERNAL_AUTH_ISSUER",
        "APP_NAMESPACE",
        "OAUTH_CLIENT_ID",
        "OAUTH_REDIRECT_URI",
        "OAUTH_JWKS_CACHE_TTL_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    for k, v in overrides.items():
        monkeypatch.setenv(k, v)


def test_local_mode_default_does_not_require_external_fields(monkeypatch):
    _clean_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.auth_provider == "local"
    assert s.external_auth_issuer is None
    assert s.oauth_client_id is None
    assert s.oauth_redirect_uri is None
    assert s.app_namespace == "inferiallm"
    assert s.oauth_jwks_cache_ttl_seconds == 3600


def test_local_mode_with_partial_fields_is_allowed(monkeypatch):
    _clean_env(
        monkeypatch,
        AUTH_PROVIDER="local",
        EXTERNAL_AUTH_URL="https://auth.example.test",
    )
    s = Settings(_env_file=None)
    assert s.auth_provider == "local"
    assert s.external_auth_url == "https://auth.example.test"


def test_external_mode_with_all_fields_present(monkeypatch):
    _clean_env(
        monkeypatch,
        AUTH_PROVIDER="external",
        EXTERNAL_AUTH_URL="https://auth.example.test",
        EXTERNAL_AUTH_ISSUER="https://auth.example.test",
        OAUTH_CLIENT_ID="inferiallm-dashboard",
        OAUTH_REDIRECT_URI="https://app.example.test/auth/callback",
    )
    s = Settings(_env_file=None)
    assert s.auth_provider == "external"
    assert s.external_auth_url == "https://auth.example.test"
    assert s.external_auth_issuer == "https://auth.example.test"
    assert s.oauth_client_id == "inferiallm-dashboard"
    assert s.oauth_redirect_uri == "https://app.example.test/auth/callback"


@pytest.mark.parametrize(
    "missing_var",
    [
        "EXTERNAL_AUTH_URL",
        "EXTERNAL_AUTH_ISSUER",
        "OAUTH_CLIENT_ID",
        "OAUTH_REDIRECT_URI",
    ],
)
def test_external_mode_rejects_missing_required_field(monkeypatch, missing_var):
    env = {
        "AUTH_PROVIDER": "external",
        "EXTERNAL_AUTH_URL": "https://auth.example.test",
        "EXTERNAL_AUTH_ISSUER": "https://auth.example.test",
        "OAUTH_CLIENT_ID": "inferiallm-dashboard",
        "OAUTH_REDIRECT_URI": "https://app.example.test/auth/callback",
    }
    env.pop(missing_var)
    _clean_env(monkeypatch, **env)
    with pytest.raises(Exception) as exc_info:
        Settings(_env_file=None)
    msg = str(exc_info.value)
    assert missing_var in msg
    assert "AUTH_PROVIDER=external" in msg


def test_external_mode_rejects_multiple_missing_fields_lists_all(monkeypatch):
    _clean_env(
        monkeypatch,
        AUTH_PROVIDER="external",
        EXTERNAL_AUTH_URL="https://auth.example.test",
    )
    with pytest.raises(Exception) as exc_info:
        Settings(_env_file=None)
    msg = str(exc_info.value)
    for var in ("EXTERNAL_AUTH_ISSUER", "OAUTH_CLIENT_ID", "OAUTH_REDIRECT_URI"):
        assert var in msg


def test_jwks_cache_ttl_lower_bound(monkeypatch):
    _clean_env(monkeypatch, OAUTH_JWKS_CACHE_TTL_SECONDS="30")
    with pytest.raises(Exception):
        Settings(_env_file=None)


def test_jwks_cache_ttl_upper_bound(monkeypatch):
    _clean_env(monkeypatch, OAUTH_JWKS_CACHE_TTL_SECONDS="999999")
    with pytest.raises(Exception):
        Settings(_env_file=None)


def test_jwks_cache_ttl_accepts_in_range_value(monkeypatch):
    _clean_env(monkeypatch, OAUTH_JWKS_CACHE_TTL_SECONDS="7200")
    s = Settings(_env_file=None)
    assert s.oauth_jwks_cache_ttl_seconds == 7200


def test_app_namespace_can_be_overridden(monkeypatch):
    _clean_env(monkeypatch, APP_NAMESPACE="otherapp")
    s = Settings(_env_file=None)
    assert s.app_namespace == "otherapp"
