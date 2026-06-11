"""Tests for InferiaConfig schema (Section 6 + 7.4 of the spec)."""
import pytest
from pydantic import ValidationError

from common.unified_config.schema import (
    InferiaConfig,
    KNOWN_PLACEHOLDER_SECRETS,
)


def _base_dict(**overrides):
    """Minimum valid input. Extend via overrides."""
    base = {
        "version": 1,
        "environment": "development",
        "log_level": "INFO",
    }
    base.update(overrides)
    return base


# ─── version ──────────────────────────────────────────────────────────────
def test_minimum_valid_loads():
    cfg = InferiaConfig.model_validate(_base_dict())
    assert cfg.version == 1
    assert cfg.environment == "development"


def test_missing_version_fails():
    with pytest.raises(ValidationError, match="version"):
        InferiaConfig.model_validate({"environment": "development"})


def test_unknown_major_version_fails():
    with pytest.raises(ValidationError, match="version"):
        InferiaConfig.model_validate(_base_dict(version=2))


# ─── environment / log_level ──────────────────────────────────────────────
def test_invalid_environment_fails():
    with pytest.raises(ValidationError):
        InferiaConfig.model_validate(_base_dict(environment="staging-eu"))


def test_invalid_log_level_fails():
    with pytest.raises(ValidationError):
        InferiaConfig.model_validate(_base_dict(log_level="VERBOSE"))


# ─── security ─────────────────────────────────────────────────────────────
def test_short_jwt_secret_fails():
    with pytest.raises(ValidationError, match="32"):
        InferiaConfig.model_validate(_base_dict(security={"jwt_secret_key": "short"}))


def test_placeholder_jwt_secret_fails():
    for placeholder in KNOWN_PLACEHOLDER_SECRETS:
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                _base_dict(security={"jwt_secret_key": placeholder})
            )


def test_valid_jwt_secret_loads():
    cfg = InferiaConfig.model_validate(
        _base_dict(security={"jwt_secret_key": "a" * 64})
    )
    assert cfg.security.jwt_secret_key == "a" * 64


def test_short_internal_api_key_fails():
    with pytest.raises(ValidationError, match="32"):
        InferiaConfig.model_validate(
            _base_dict(security={"internal_api_key": "tiny"})
        )


# ─── services ─────────────────────────────────────────────────────────────
def test_service_enabled_must_be_bool():
    with pytest.raises(ValidationError):
        InferiaConfig.model_validate(
            _base_dict(services={"api_gateway": {"enabled": "not_a_bool"}})
        )


def test_service_port_no_longer_in_schema():
    """port is a hosting field → env only. Yaml schema ignores it (extra='ignore' at root,
    but api_gateway has extra='forbid', so supplying port now raises ValidationError."""
    with pytest.raises(ValidationError):
        InferiaConfig.model_validate(
            _base_dict(services={"api_gateway": {"port": 8000}})
        )


def test_unknown_field_inside_service_fails():
    with pytest.raises(ValidationError, match="typo_field_should_fail"):
        InferiaConfig.model_validate(
            _base_dict(services={"api_gateway": {"typo_field_should_fail": "oops"}})
        )


# ─── security: None secrets are allowed (unset) ───────────────────────────
def test_none_jwt_secret_allowed():
    cfg = InferiaConfig.model_validate(
        _base_dict(security={"jwt_secret_key": None})
    )
    assert cfg.security.jwt_secret_key is None


def test_none_internal_api_key_allowed():
    cfg = InferiaConfig.model_validate(
        _base_dict(security={"internal_api_key": None})
    )
    assert cfg.security.internal_api_key is None


# ─── unknown top-level: warning, not fatal ────────────────────────────────
def test_unknown_top_level_key_does_not_fail():
    cfg = InferiaConfig.model_validate(_base_dict(weird_future_key=1))
    # No exception; entry just isn't kept on the model.
    assert not hasattr(cfg, "weird_future_key")


# ─── providers are DB-managed — no providers field on InferiaConfig ───────────
class TestProvidersNotInSchema:
    """providers: block is no longer in the yaml schema.
    The field must not exist on InferiaConfig; unknown top-level keys are
    silently ignored (extra='ignore' on the root model).
    """

    def test_providers_field_absent_from_inferia_config(self):
        assert "providers" not in InferiaConfig.model_fields

    def test_providers_block_in_yaml_is_silently_ignored(self):
        """A yaml file that still has a providers: block must not fail validation."""
        cfg = InferiaConfig.model_validate(
            _base_dict(providers={"aws": {"access_key_id": "AKIA"}})
        )
        # Root model has extra='ignore', so it is just dropped — no exception.
        assert not hasattr(cfg, "providers")
