"""Tests for api_gateway.config — external auth validator and new fields.

Per plan C1: when AUTH_PROVIDER=external is set, four downstream OAuth/external
fields must all be present (EXTERNAL_AUTH_URL, EXTERNAL_AUTH_ISSUER,
OAUTH_CLIENT_ID, OAUTH_REDIRECT_URI). Local mode should leave them all optional.
"""

import os
import types
from typing import Iterator
import pytest

from inferia.services.api_gateway.config import Settings, httpx_verify


def _clean_env(monkeypatch, **overrides) -> None:
    """Strip all relevant env vars then apply overrides.

    Includes INFERIA_CONFIG because other test modules in the repo leak it
    (set in fixtures without cleanup), and our Settings init walks the
    unified config loader which would otherwise fail on a stale path.
    """
    for key in (
        "AUTH_PROVIDER",
        "EXTERNAL_AUTH_URL",
        "EXTERNAL_AUTH_ISSUER",
        "APP_NAMESPACE",
        "OAUTH_CLIENT_ID",
        "OAUTH_REDIRECT_URI",
        "OAUTH_JWKS_CACHE_TTL_SECONDS",
        "INFERIA_CONFIG",
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
    # `external` is a deprecated alias that coerces to the canonical `inferiaauth`.
    assert s.auth_provider == "inferiaauth"
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
    # `external` coerces to `inferiaauth`, so the error references the canonical mode.
    assert "AUTH_PROVIDER=inferiaauth" in msg


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


# ---------------------------------------------------------------------------
# httpx_verify helper — Bug-2
# ---------------------------------------------------------------------------


def test_httpx_verify_returns_ca_bundle_when_set():
    """When ssl_ca_bundle is set, httpx_verify returns the path string."""
    cfg = types.SimpleNamespace(ssl_ca_bundle="/etc/ssl/ca.pem", verify_ssl=True)
    assert httpx_verify(cfg) == "/etc/ssl/ca.pem"


def test_httpx_verify_returns_verify_ssl_true_when_no_bundle():
    """When ssl_ca_bundle is None, httpx_verify returns verify_ssl (True)."""
    cfg = types.SimpleNamespace(ssl_ca_bundle=None, verify_ssl=True)
    assert httpx_verify(cfg) is True


def test_httpx_verify_returns_verify_ssl_false_when_no_bundle():
    """When ssl_ca_bundle is None and verify_ssl=False, httpx_verify returns False."""
    cfg = types.SimpleNamespace(ssl_ca_bundle=None, verify_ssl=False)
    assert httpx_verify(cfg) is False


def test_httpx_verify_returns_bundle_over_false_verify_ssl():
    """CA bundle takes precedence even when verify_ssl=False."""
    cfg = types.SimpleNamespace(ssl_ca_bundle="/custom/ca.pem", verify_ssl=False)
    assert httpx_verify(cfg) == "/custom/ca.pem"


def test_external_service_id_defaults_none(monkeypatch):
    """external_service_id defaults to None when EXTERNAL_SERVICE_ID is unset."""
    _clean_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.external_service_id is None


def test_external_service_id_can_be_set(monkeypatch):
    """EXTERNAL_SERVICE_ID env var populates external_service_id."""
    _clean_env(monkeypatch, EXTERNAL_SERVICE_ID="18796444-5076-4a29-832a-dba5f876cb56")
    s = Settings(_env_file=None)
    assert s.external_service_id == "18796444-5076-4a29-832a-dba5f876cb56"
