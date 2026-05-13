"""Tests for InferiaConfig schema (Section 6 + 7.4 of the spec)."""
import pytest
from pydantic import ValidationError

from inferia.common.unified_config.schema import (
    InferiaConfig,
    KNOWN_PLACEHOLDER_SECRETS,
    ProvidersConfig,
    AWSProvider,
    GCPProvider,
    AzureProvider,
    IBMProvider,
    NosanaProvider,
    NosanaApiKeyEntry,
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


# ─── providers schema ─────────────────────────────────────────────────────
class TestProvidersSchema:
    """ProvidersConfig is now a typed flat schema (extra='forbid')."""

    def test_default_providers_all_none(self):
        cfg = InferiaConfig.model_validate(_base_dict())
        p = cfg.providers
        assert p.aws.access_key_id is None
        assert p.gcp.project_id is None
        assert p.azure.subscription_id is None
        assert p.ibm.api_key is None
        assert p.nosana.wallet_private_key is None
        assert p.nosana.api_keys == []

    def test_aws_fields_load(self):
        cfg = InferiaConfig.model_validate(
            _base_dict(
                providers={
                    "aws": {
                        "access_key_id": "AKIAIOSFODNN",
                        "secret_access_key": "wJalrXUtnFEMI",
                        "region": "eu-west-1",
                    }
                }
            )
        )
        assert cfg.providers.aws.access_key_id == "AKIAIOSFODNN"
        assert cfg.providers.aws.secret_access_key == "wJalrXUtnFEMI"
        assert cfg.providers.aws.region == "eu-west-1"

    def test_aws_unknown_field_fails(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                _base_dict(providers={"aws": {"unknown_field": "oops"}})
            )

    def test_gcp_fields_load(self):
        cfg = InferiaConfig.model_validate(
            _base_dict(
                providers={
                    "gcp": {
                        "project_id": "my-project",
                        "service_account_json": '{"type":"service_account"}',
                        "region": "us-central1",
                    }
                }
            )
        )
        assert cfg.providers.gcp.project_id == "my-project"
        assert cfg.providers.gcp.region == "us-central1"

    def test_azure_fields_load(self):
        cfg = InferiaConfig.model_validate(
            _base_dict(
                providers={
                    "azure": {
                        "subscription_id": "sub-abc",
                        "tenant_id": "ten-xyz",
                        "client_id": "cid",
                        "client_secret": "csec",
                        "region": "eastus",
                    }
                }
            )
        )
        p = cfg.providers.azure
        assert p.subscription_id == "sub-abc"
        assert p.tenant_id == "ten-xyz"
        assert p.client_id == "cid"
        assert p.client_secret == "csec"

    def test_azure_unknown_field_fails(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                _base_dict(providers={"azure": {"bad_field": "x"}})
            )

    def test_ibm_fields_load(self):
        cfg = InferiaConfig.model_validate(
            _base_dict(
                providers={
                    "ibm": {
                        "api_key": "ibm-api-key",
                        "region": "us-south",
                        "resource_group_id": "rg-123",
                    }
                }
            )
        )
        assert cfg.providers.ibm.api_key == "ibm-api-key"
        assert cfg.providers.ibm.resource_group_id == "rg-123"

    def test_ibm_unknown_field_fails(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                _base_dict(providers={"ibm": {"nonexistent": "x"}})
            )

    def test_nosana_wallet_and_api_keys_load(self):
        cfg = InferiaConfig.model_validate(
            _base_dict(
                providers={
                    "nosana": {
                        "wallet_private_key": "wallet-secret",
                        "api_keys": [
                            {"name": "prod", "key": "pk", "is_active": True},
                            {"name": "staging", "key": "sk"},
                        ],
                    }
                }
            )
        )
        n = cfg.providers.nosana
        assert n.wallet_private_key == "wallet-secret"
        assert len(n.api_keys) == 2
        assert n.api_keys[0].name == "prod"
        assert n.api_keys[1].name == "staging"

    def test_nosana_api_key_entry_requires_name_and_key(self):
        with pytest.raises(ValidationError):
            NosanaApiKeyEntry.model_validate({"key": "only-key"})  # missing name

    def test_nosana_unknown_field_fails(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                _base_dict(providers={"nosana": {"bad_field": "x"}})
            )

    def test_providers_unknown_provider_fails(self):
        """extra='forbid' means an unrecognised provider key should raise."""
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                _base_dict(providers={"akash": {"mnemonic": "word1 word2"}})
            )

    def test_providers_defaults_have_correct_regions(self):
        cfg = InferiaConfig.model_validate(_base_dict())
        assert cfg.providers.aws.region == "us-east-1"
        assert cfg.providers.gcp.region == "us-central1"
        assert cfg.providers.azure.region == "eastus"
        assert cfg.providers.ibm.region == "us-south"

    def test_nosana_api_key_entry_default_is_active_true(self):
        entry = NosanaApiKeyEntry.model_validate({"name": "prod", "key": "k"})
        assert entry.is_active is True
