"""Integration tests: each migrated service Settings loads correctly from
inferia.yaml.example via INFERIA_CONFIG.

These tests assert that the yaml → YamlConfigSettingsSource → service Settings
pipeline works end to end for each migrated service. They do NOT test every
field — just enough representative fields to catch schema mismatches and
flatten-mapping errors.

Split rule: yaml carries app behavior only; hosting/port/URL/connection fields
live in env vars. Accordingly, the tests below no longer assert on port/host/
api_gateway_url/redis_url fields from yaml — those come from env.
"""
import os
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).parents[6]  # …/InferiaLLM
EXAMPLE_YAML = REPO_ROOT / "inferia.yaml.example"

# Secrets long enough to pass the schema validator's >=32-char check.
_JWT = "dev-jwt-secret-thirty-two-bytes-min"
_IAK = "dev-internal-api-key-thirty-two-bytes"


@pytest.fixture(autouse=True)
def _yaml_env(monkeypatch, tmp_path):
    """Point INFERIA_CONFIG at a copy of inferia.yaml.example with secrets interpolated."""
    from common.unified_config.loader import _clear_cache

    # Write a version of the example yaml with secrets filled in.
    content = EXAMPLE_YAML.read_text()
    yaml_copy = tmp_path / "inferia.yaml"
    yaml_copy.write_text(content)

    monkeypatch.setenv("INFERIA_CONFIG", str(yaml_copy))
    monkeypatch.setenv("JWT_SECRET_KEY", _JWT)
    monkeypatch.setenv("INTERNAL_API_KEY", _IAK)
    _clear_cache()
    yield
    _clear_cache()


# ─── inference ───────────────────────────────────────────────────────────────

class TestInferenceSettingsFromYaml:
    def test_upstream_timeout_read_from_yaml(self):
        from inference.config import Settings
        s = Settings(_env_file=None)
        # upstream.http_timeout_seconds → flattened to upstream_http_timeout_seconds
        assert s.upstream_http_timeout_seconds == 60.0

    def test_context_cache_ttl_read_from_yaml(self):
        from inference.config import Settings
        s = Settings(_env_file=None)
        assert s.context_cache_ttl == 30

    def test_allowed_origins_coerced_from_list(self, monkeypatch):
        """security.allowed_origins is a list in yaml; field must be a str."""
        from inference.config import Settings
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        s = Settings(_env_file=None)
        # allowed_origins comes from shared security subtree as a list; must be str
        assert isinstance(s.allowed_origins, str)

    def test_env_overrides_yaml_workers(self, monkeypatch):
        from common.unified_config.loader import _clear_cache
        monkeypatch.setenv("INFERENCE_WORKERS", "4")
        _clear_cache()
        from inference.config import Settings
        s = Settings(_env_file=None)
        assert s.workers == 4

    def test_request_timeout_read_from_yaml(self):
        from inference.config import Settings
        s = Settings(_env_file=None)
        assert s.request_timeout == 30

    def test_upstream_max_response_bytes_from_yaml(self):
        from inference.config import Settings
        s = Settings(_env_file=None)
        assert s.upstream_max_response_bytes == 52_428_800

    def test_context_cache_maxsize_from_yaml(self):
        from inference.config import Settings
        s = Settings(_env_file=None)
        assert s.context_cache_maxsize == 1000


# ─── orchestration ───────────────────────────────────────────────────────────

class TestOrchestrationSettingsFromYaml:
    def test_readiness_timeout_from_yaml(self):
        from orchestration.config import Settings
        s = Settings(_env_file=None)
        assert s.default_readiness_timeout == 300

    def test_deployment_log_buffer_size_from_yaml(self):
        from orchestration.config import Settings
        s = Settings(_env_file=None)
        assert s.deployment_log_buffer_size == 10000

    def test_ephemeral_failure_threshold_from_yaml(self):
        from orchestration.config import Settings
        s = Settings(_env_file=None)
        assert s.ephemeral_failure_threshold_minutes == 10

    def test_deployment_log_flush_interval_from_yaml(self):
        from orchestration.config import Settings
        s = Settings(_env_file=None)
        assert s.deployment_log_flush_interval == 10

    def test_default_polling_interval_from_yaml(self):
        from orchestration.config import Settings
        s = Settings(_env_file=None)
        assert s.default_polling_interval == 20
