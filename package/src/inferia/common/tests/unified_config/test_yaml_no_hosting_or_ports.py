"""Contract test: inferia.yaml.example must not contain any hosting or port fields.

After the env-vs-yaml split, yaml owns application behavior only.  Hosting
fields (host, port, workers, reload), URL fields (api_gateway_url,
service_urls, dashboard URLs, database URL, redis_url), SSL fields, and
connection-credential fields must NOT appear in the yaml schema or the example
file.

These tests load the schema and the example file and assert the contract holds.
"""
import os
from pathlib import Path
import pytest
from pydantic import ValidationError

from inferia.common.unified_config.schema import (
    InferiaConfig,
    ApiGatewayService,
    InferenceService,
    GuardrailService,
    DataService,
    OrchestrationService,
    ServicesConfig,
)

REPO_ROOT = Path(__file__).parents[6]  # …/InferiaLLM
EXAMPLE_YAML = REPO_ROOT / "inferia.yaml.example"

# Secrets long enough to pass the schema validator's >=32-char check.
_JWT = "dev-jwt-secret-thirty-two-bytes-min"
_IAK = "dev-internal-api-key-thirty-two-bytes"


# ─── Schema model attribute checks ────────────────────────────────────────────

class TestSchemaHasNoHostingFields:
    """The schema model classes must not declare host/port/URL attributes."""

    HOSTING_ATTRS = (
        "host", "port", "http_port", "grpc_port", "workers", "reload",
        "proxy_headers", "forwarded_allow_ips",
    )
    URL_ATTRS = (
        "api_gateway_url", "external_proxy_url", "redis_url",
        "service_urls", "dashboard",
    )
    SSL_ATTRS = ("ssl",)

    @pytest.mark.parametrize("attr", HOSTING_ATTRS + URL_ATTRS + SSL_ATTRS)
    def test_api_gateway_service_has_no_hosting_attr(self, attr):
        assert attr not in ApiGatewayService.model_fields, (
            f"ApiGatewayService.{attr} must not be in yaml schema (hosting/URL → env only)"
        )

    @pytest.mark.parametrize("attr", ("host", "port", "workers", "reload",
                                       "api_gateway_url", "external_proxy_url", "ssl"))
    def test_inference_service_has_no_hosting_attr(self, attr):
        assert attr not in InferenceService.model_fields, (
            f"InferenceService.{attr} must not be in yaml schema (hosting/URL → env only)"
        )

    @pytest.mark.parametrize("attr", ("host", "port", "reload",
                                       "api_gateway_url", "allowed_origins"))
    def test_guardrail_service_has_no_hosting_attr(self, attr):
        assert attr not in GuardrailService.model_fields, (
            f"GuardrailService.{attr} must not be in yaml schema (hosting/URL → env only)"
        )

    @pytest.mark.parametrize("attr", ("host", "port", "reload",
                                       "api_gateway_url", "redis_url", "allowed_origins"))
    def test_data_service_has_no_hosting_attr(self, attr):
        assert attr not in DataService.model_fields, (
            f"DataService.{attr} must not be in yaml schema (hosting/URL → env only)"
        )

    @pytest.mark.parametrize("attr", ("host", "http_port", "grpc_port",
                                       "api_gateway_database_url"))
    def test_orchestration_service_has_no_hosting_attr(self, attr):
        assert attr not in OrchestrationService.model_fields, (
            f"OrchestrationService.{attr} must not be in yaml schema (hosting/URL → env only)"
        )

    def test_inferia_config_has_no_infra_field(self):
        """InfraConfig (database, redis, logstash) entirely removed from yaml schema."""
        assert "infra" not in InferiaConfig.model_fields, (
            "InferiaConfig.infra must not be in yaml schema (all infra → env only)"
        )


# ─── Example file parse checks ────────────────────────────────────────────────

class TestExampleYamlParsesCleanly:
    """inferia.yaml.example must parse cleanly under the new schema."""

    @pytest.fixture(autouse=True)
    def _set_secrets(self, monkeypatch, tmp_path):
        from inferia.common.unified_config.loader import _clear_cache
        content = EXAMPLE_YAML.read_text()
        yaml_copy = tmp_path / "inferia.yaml"
        yaml_copy.write_text(content)
        monkeypatch.setenv("INFERIA_CONFIG", str(yaml_copy))
        monkeypatch.setenv("JWT_SECRET_KEY", _JWT)
        monkeypatch.setenv("INTERNAL_API_KEY", _IAK)
        _clear_cache()
        yield
        _clear_cache()

    def test_example_yaml_loads_without_error(self):
        from inferia.common.unified_config.loader import load_unified_config
        cfg = load_unified_config()
        assert cfg is not None
        assert cfg.version == 1

    def test_loaded_config_has_no_host_on_api_gateway(self):
        from inferia.common.unified_config.loader import load_unified_config
        cfg = load_unified_config()
        assert not hasattr(cfg.services.api_gateway, "host")

    def test_loaded_config_has_no_port_on_api_gateway(self):
        from inferia.common.unified_config.loader import load_unified_config
        cfg = load_unified_config()
        assert not hasattr(cfg.services.api_gateway, "port")

    def test_loaded_config_has_no_service_urls(self):
        from inferia.common.unified_config.loader import load_unified_config
        cfg = load_unified_config()
        assert not hasattr(cfg.services.api_gateway, "service_urls")

    def test_loaded_config_has_no_dashboard_block(self):
        from inferia.common.unified_config.loader import load_unified_config
        cfg = load_unified_config()
        assert not hasattr(cfg.services.api_gateway, "dashboard")

    def test_loaded_config_has_no_infra(self):
        from inferia.common.unified_config.loader import load_unified_config
        cfg = load_unified_config()
        assert not hasattr(cfg, "infra")


# ─── Schema rejects hosting fields if supplied in yaml ────────────────────────

class TestSchemaRejectsHostingFields:
    """Supplying a removed hosting field in yaml must raise a ValidationError
    (extra='forbid' on each service model)."""

    def _base(self, **overrides):
        base = {"version": 1, "environment": "development", "log_level": "INFO"}
        base.update(overrides)
        return base

    def test_api_gateway_port_rejected(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                self._base(services={"api_gateway": {"port": 8000}})
            )

    def test_api_gateway_host_rejected(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                self._base(services={"api_gateway": {"host": "0.0.0.0"}})
            )

    def test_api_gateway_workers_rejected(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                self._base(services={"api_gateway": {"workers": 2}})
            )

    def test_api_gateway_ssl_rejected(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                self._base(services={"api_gateway": {"ssl": {"verify": True}}})
            )

    def test_api_gateway_service_urls_rejected(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                self._base(services={"api_gateway": {"service_urls": {"guardrail": "http://localhost:8002"}}})
            )

    def test_api_gateway_dashboard_rejected(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                self._base(services={"api_gateway": {"dashboard": {"api_gateway_url": "http://gw:8000"}}})
            )

    def test_inference_port_rejected(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                self._base(services={"inference": {"port": 8001}})
            )

    def test_inference_api_gateway_url_rejected(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                self._base(services={"inference": {"api_gateway_url": "http://localhost:8000"}})
            )

    def test_guardrail_port_rejected(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                self._base(services={"guardrail": {"port": 8002}})
            )

    def test_data_port_rejected(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                self._base(services={"data": {"port": 8003}})
            )

    def test_data_redis_url_rejected(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                self._base(services={"data": {"redis_url": "redis://localhost:6379/0"}})
            )

    def test_orchestration_http_port_rejected(self):
        with pytest.raises(ValidationError):
            InferiaConfig.model_validate(
                self._base(services={"orchestration": {"http_port": 8080}})
            )

    def test_infra_block_ignored_at_root(self):
        """The root model has extra='ignore' so an infra block in yaml is silently dropped."""
        cfg = InferiaConfig.model_validate(
            self._base(infra={"database": {"url": "postgresql://localhost/inferia"}})
        )
        assert not hasattr(cfg, "infra")
