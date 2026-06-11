"""Tests for the three-mode auth_provider config (local | oidc | inferiaauth).

Run with --noconftest to avoid the jwt import conflict in conftest.py:
    python -m pytest src/services/api_gateway/tests/test_config_modes.py -v --noconftest
"""

import warnings
import pytest
from services.api_gateway.config import Settings

# ---- Shared helper: the four external-auth fields ----
EXT = dict(
    external_auth_url="https://auth.example.com",
    external_auth_issuer="https://auth.example.com",
    oauth_client_id="client-id-abc",
    oauth_redirect_uri="https://myapp.example.com/auth/callback",
)

JWT = dict(jwt_secret_key="x" * 32)


# ---------------------------------------------------------------------------
# local mode
# ---------------------------------------------------------------------------


def test_local_requires_nothing():
    s = Settings(auth_provider="local", **JWT)
    assert s.auth_provider == "local"
    assert s.is_external_mode is False


def test_local_ignores_external_fields_when_provided():
    """local mode should still accept (and ignore) external fields gracefully."""
    s = Settings(auth_provider="local", **JWT, **EXT)
    assert s.auth_provider == "local"
    assert s.is_external_mode is False


# ---------------------------------------------------------------------------
# inferiaauth mode
# ---------------------------------------------------------------------------


def test_inferiaauth_requires_external_fields():
    with pytest.raises(ValueError) as exc_info:
        Settings(auth_provider="inferiaauth", **JWT)
    err = str(exc_info.value)
    assert "EXTERNAL_AUTH_URL" in err


def test_inferiaauth_error_names_all_missing_fields():
    """All four missing fields must be named in the single error."""
    with pytest.raises(ValueError) as exc_info:
        Settings(auth_provider="inferiaauth", **JWT)
    err = str(exc_info.value)
    assert "EXTERNAL_AUTH_URL" in err
    assert "EXTERNAL_AUTH_ISSUER" in err
    assert "OAUTH_CLIENT_ID" in err
    assert "OAUTH_REDIRECT_URI" in err


def test_inferiaauth_error_mentions_provider_name():
    """Error message should indicate the active provider name."""
    with pytest.raises(ValueError) as exc_info:
        Settings(auth_provider="inferiaauth", **JWT)
    assert "inferiaauth" in str(exc_info.value)


def test_inferiaauth_ok_with_fields():
    s = Settings(auth_provider="inferiaauth", **JWT, **EXT)
    assert s.auth_provider == "inferiaauth"
    assert s.is_external_mode is True


def test_inferiaauth_partial_fields_error():
    """Providing only some of the four fields should still raise."""
    partial = dict(external_auth_url="https://auth.example.com")
    with pytest.raises(ValueError) as exc_info:
        Settings(auth_provider="inferiaauth", **JWT, **partial)
    err = str(exc_info.value)
    assert "EXTERNAL_AUTH_ISSUER" in err
    assert "OAUTH_CLIENT_ID" in err
    assert "OAUTH_REDIRECT_URI" in err


# ---------------------------------------------------------------------------
# oidc mode
# ---------------------------------------------------------------------------


def test_oidc_requires_external_fields():
    with pytest.raises(ValueError) as exc_info:
        Settings(auth_provider="oidc", **JWT)
    err = str(exc_info.value)
    assert "EXTERNAL_AUTH_URL" in err


def test_oidc_error_names_all_missing_fields():
    with pytest.raises(ValueError) as exc_info:
        Settings(auth_provider="oidc", **JWT)
    err = str(exc_info.value)
    assert "EXTERNAL_AUTH_URL" in err
    assert "EXTERNAL_AUTH_ISSUER" in err
    assert "OAUTH_CLIENT_ID" in err
    assert "OAUTH_REDIRECT_URI" in err


def test_oidc_error_mentions_provider_name():
    with pytest.raises(ValueError) as exc_info:
        Settings(auth_provider="oidc", **JWT)
    assert "oidc" in str(exc_info.value)


def test_oidc_ok_with_fields():
    s = Settings(auth_provider="oidc", **JWT, **EXT)
    assert s.auth_provider == "oidc"
    assert s.is_external_mode is True


def test_oidc_partial_fields_error():
    partial = dict(external_auth_url="https://auth.example.com", external_auth_issuer="https://auth.example.com")
    with pytest.raises(ValueError) as exc_info:
        Settings(auth_provider="oidc", **JWT, **partial)
    err = str(exc_info.value)
    assert "OAUTH_CLIENT_ID" in err
    assert "OAUTH_REDIRECT_URI" in err


# ---------------------------------------------------------------------------
# is_external_mode property
# ---------------------------------------------------------------------------


def test_is_external_mode_local_is_false():
    s = Settings(auth_provider="local", **JWT)
    assert s.is_external_mode is False


def test_is_external_mode_inferiaauth_is_true():
    s = Settings(auth_provider="inferiaauth", **JWT, **EXT)
    assert s.is_external_mode is True


def test_is_external_mode_oidc_is_true():
    s = Settings(auth_provider="oidc", **JWT, **EXT)
    assert s.is_external_mode is True


# ---------------------------------------------------------------------------
# backward-compat: "external" → "inferiaauth"
# ---------------------------------------------------------------------------


def test_external_alias_maps_to_inferiaauth():
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        s = Settings(auth_provider="external", **JWT, **EXT)
    assert s.auth_provider == "inferiaauth"


def test_external_alias_emits_deprecation_warning():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        Settings(auth_provider="external", **JWT, **EXT)
    assert any(issubclass(x.category, DeprecationWarning) for x in w), (
        "Expected a DeprecationWarning when AUTH_PROVIDER=external"
    )


def test_external_alias_with_fields_is_external_mode():
    """After alias mapping, the resulting object should be external_mode=True."""
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        s = Settings(auth_provider="external", **JWT, **EXT)
    assert s.is_external_mode is True


def test_external_alias_without_fields_still_raises():
    """Alias maps correctly, then external-field validation fires."""
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        with pytest.raises(ValueError) as exc_info:
            Settings(auth_provider="external", **JWT)
    err = str(exc_info.value)
    # After alias mapping auth_provider is "inferiaauth"; the error should name it
    assert "inferiaauth" in err


def test_external_alias_maps_to_inferiaauth_with_warning():
    """Canonical test from spec: alias + warning together."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        s = Settings(auth_provider="external", **JWT, **EXT)
    assert s.auth_provider == "inferiaauth"
    assert any(issubclass(x.category, DeprecationWarning) for x in w)


# ---------------------------------------------------------------------------
# invalid value rejected
# ---------------------------------------------------------------------------


def test_invalid_auth_provider_rejected():
    with pytest.raises((ValueError, Exception)):
        Settings(auth_provider="unknown_mode", **JWT)


# ---------------------------------------------------------------------------
# default
# ---------------------------------------------------------------------------


def test_default_auth_provider_is_local():
    """With no auth_provider kwarg the default must be 'local'."""
    s = Settings(**JWT)
    assert s.auth_provider == "local"
    assert s.is_external_mode is False
