"""Tests for YamlConfigSettingsSource (Section 8.2 of the spec)."""
from typing import ClassVar, Optional
from pydantic_settings import BaseSettings

from common.unified_config.source import YamlConfigSettingsSource
from common.unified_config.loader import _clear_cache


class _Demo(BaseSettings):
    """Stand-in for a service Settings — exercised in isolation."""
    _yaml_path: ClassVar[Optional[str]] = "services.api_gateway"
    port: int = 8000
    jwt_secret_key: Optional[str] = None
    redis_host: str = "localhost"


def test_no_yaml_returns_empty(tmp_path, monkeypatch, clean_env):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "common.unified_config.loader._SYSTEM_PATH",
        tmp_path / "missing.yaml",
    )
    _clear_cache()
    source = YamlConfigSettingsSource(_Demo)
    assert source() == {}


def test_service_field_read_from_yaml(fixtures_dir, monkeypatch, clean_env):
    monkeypatch.setenv("INFERIA_CONFIG", str(fixtures_dir / "valid.yaml"))
    _clear_cache()
    source = YamlConfigSettingsSource(_Demo)
    out = source()
    # port is no longer in yaml (hosting → env only); not present in output
    assert "port" not in out
    assert out["jwt_secret_key"]         # from security.jwt_secret_key (merged)
    # redis_host was in infra.redis (infra → env only); not present in output
    assert "redis_host" not in out


def test_unknown_yaml_path_returns_empty(fixtures_dir, monkeypatch, clean_env):
    class _Other(BaseSettings):
        _yaml_path: ClassVar[Optional[str]] = "services.does_not_exist"
        port: int = 0

    monkeypatch.setenv("INFERIA_CONFIG", str(fixtures_dir / "valid.yaml"))
    _clear_cache()
    source = YamlConfigSettingsSource(_Other)
    assert source() == {}


def test_no_yaml_path_returns_empty(fixtures_dir, monkeypatch, clean_env):
    class _NoPath(BaseSettings):
        port: int = 0

    monkeypatch.setenv("INFERIA_CONFIG", str(fixtures_dir / "valid.yaml"))
    _clear_cache()
    source = YamlConfigSettingsSource(_NoPath)
    assert source() == {}


def test_top_level_scalars_environment_and_log_level(fixtures_dir, monkeypatch, clean_env):
    """Lines 86/88: environment and log_level are injected when declared as fields."""
    class _WithTopLevel(BaseSettings):
        _yaml_path: ClassVar[Optional[str]] = "services.api_gateway"
        port: int = 0
        environment: str = "production"
        log_level: str = "ERROR"

    monkeypatch.setenv("INFERIA_CONFIG", str(fixtures_dir / "valid.yaml"))
    _clear_cache()
    source = YamlConfigSettingsSource(_WithTopLevel)
    out = source()
    assert out["environment"] == "development"   # from valid.yaml
    assert out["log_level"] == "INFO"             # from valid.yaml


def test_get_field_value_present_field(fixtures_dir, monkeypatch, clean_env):
    """get_field_value returns value, name, False for a known field present in yaml.
    jwt_secret_key is in security (merged) and valid.yaml has it set, so it resolves.
    """
    monkeypatch.setenv("INFERIA_CONFIG", str(fixtures_dir / "valid.yaml"))
    _clear_cache()
    source = YamlConfigSettingsSource(_Demo)
    field_info = _Demo.model_fields["jwt_secret_key"]
    value, name, is_complex = source.get_field_value(field_info, "jwt_secret_key")
    assert value == "this-is-a-thirty-two-byte-test-secret-key"
    assert name == "jwt_secret_key"
    assert is_complex is False


def test_get_field_value_absent_field(fixtures_dir, monkeypatch, clean_env):
    """get_field_value returns None, name, False for an unknown field."""
    monkeypatch.setenv("INFERIA_CONFIG", str(fixtures_dir / "valid.yaml"))
    _clear_cache()
    source = YamlConfigSettingsSource(_Demo)
    field_info = _Demo.model_fields["port"]
    value, name, is_complex = source.get_field_value(field_info, "nonexistent_field")
    assert value is None
    assert name == "nonexistent_field"
    assert is_complex is False


def test_flatten_with_none_node(tmp_path, monkeypatch, clean_env):
    """Line 45: _flatten(None) returns empty dict without error."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "common.unified_config.loader._SYSTEM_PATH",
        tmp_path / "missing.yaml",
    )
    _clear_cache()
    source = YamlConfigSettingsSource(_Demo)
    # _flatten is internal but we can access it; passing None triggers line 45
    result = source._flatten(None)
    assert result == {}
