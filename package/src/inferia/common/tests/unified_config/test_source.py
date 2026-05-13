"""Tests for YamlConfigSettingsSource (Section 8.2 of the spec)."""
from typing import ClassVar, Optional
import pytest
from pydantic_settings import BaseSettings

from inferia.common.unified_config.source import YamlConfigSettingsSource
from inferia.common.unified_config.loader import _clear_cache


class _Demo(BaseSettings):
    """Stand-in for a service Settings — exercised in isolation."""
    _yaml_path: ClassVar[Optional[str]] = "services.api_gateway"
    port: int = 8000
    jwt_secret_key: Optional[str] = None
    redis_host: str = "localhost"


def test_no_yaml_returns_empty(tmp_path, monkeypatch, clean_env):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "inferia.common.unified_config.loader._SYSTEM_PATH",
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
    assert out["port"] == 8000           # from services.api_gateway.port
    assert out["jwt_secret_key"]         # from security.jwt_secret_key (merged)
    assert out["redis_host"] == "localhost"  # from infra.redis.host (merged + flattened)


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
    """Line 95-96: get_field_value returns value, name, False for a known field."""
    monkeypatch.setenv("INFERIA_CONFIG", str(fixtures_dir / "valid.yaml"))
    _clear_cache()
    source = YamlConfigSettingsSource(_Demo)
    field_info = _Demo.model_fields["port"]
    value, name, is_complex = source.get_field_value(field_info, "port")
    assert value == 8000
    assert name == "port"
    assert is_complex is False


def test_get_field_value_absent_field(fixtures_dir, monkeypatch, clean_env):
    """Line 97: get_field_value returns None, name, False for an unknown field."""
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
        "inferia.common.unified_config.loader._SYSTEM_PATH",
        tmp_path / "missing.yaml",
    )
    _clear_cache()
    source = YamlConfigSettingsSource(_Demo)
    # _flatten is internal but we can access it; passing None triggers line 45
    result = source._flatten(None)
    assert result == {}
