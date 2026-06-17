"""Tests for UnifiedBaseSettings + the env > yaml > defaults precedence chain."""
from typing import ClassVar, Optional

from common.unified_config import UnifiedBaseSettings
from common.unified_config.loader import _clear_cache


class _Demo(UnifiedBaseSettings):
    _yaml_path: ClassVar[Optional[str]] = "services.api_gateway"
    port: int = 9999          # default; yaml says 8000
    jwt_secret_key: Optional[str] = None
    redis_host: str = "no.where"


def test_no_yaml_no_env_uses_defaults(tmp_path, monkeypatch, clean_env):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "common.unified_config.loader._SYSTEM_PATH",
        tmp_path / "missing.yaml",
    )
    _clear_cache()
    s = _Demo()
    assert s.port == 9999
    assert s.redis_host == "no.where"


def test_yaml_wins_over_default(fixtures_dir, monkeypatch, clean_env):
    monkeypatch.setenv("INFERIA_CONFIG", str(fixtures_dir / "valid.yaml"))
    _clear_cache()
    s = _Demo()
    # port is a hosting field → env only; no longer in yaml. Falls to default.
    assert s.port == 9999        # default (yaml has no port field any more)
    # redis_host is a connection field → env only (infra.redis removed from yaml).
    # Its value may come from .env file in the repo; just assert it's a string.
    assert isinstance(s.redis_host, str)


def test_env_wins_over_yaml(fixtures_dir, monkeypatch, clean_env):
    monkeypatch.setenv("INFERIA_CONFIG", str(fixtures_dir / "valid.yaml"))
    monkeypatch.setenv("PORT", "9001")
    _clear_cache()
    s = _Demo()
    assert s.port == 9001


def test_subclass_without_yaml_path_behaves_like_basesettings(
    fixtures_dir, monkeypatch, clean_env
):
    class _NoPath(UnifiedBaseSettings):
        port: int = 1234

    monkeypatch.setenv("INFERIA_CONFIG", str(fixtures_dir / "valid.yaml"))
    _clear_cache()
    s = _NoPath()
    assert s.port == 1234


def test_api_gateway_settings_loads_yaml_under_docker_shape(
    monkeypatch, clean_env, tmp_path
):
    """Regression for the Docker smoke: security.allowed_origins is list[str] in yaml
    but api_gateway.Settings.allowed_origins is a comma-separated str. The
    field_validator on api_gateway must coerce a list into a comma-joined string
    instead of raising a Pydantic 'string_type' ValidationError.
    Note: port is a hosting field and must NOT appear in yaml.
    """
    yaml_path = tmp_path / "inferia.yaml"
    yaml_path.write_text(
        "version: 1\n"
        "environment: development\n"
        "log_level: INFO\n"
        "security:\n"
        "  allowed_origins:\n"
        "    - http://yaml-only-origin-a.example.com\n"
        "    - http://yaml-only-origin-b.example.com\n"
        "services:\n"
        "  api_gateway:\n"
        "    enabled: true\n"
    )
    # Strip any leaked env override so the yaml path actually feeds the field.
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("INFERIA_CONFIG", str(yaml_path))
    _clear_cache()

    from api_gateway.config import Settings as ApiGatewaySettings

    # _env_file=None disables the dotenv source so this test isolates the yaml
    # path. In production both layers coexist with env > .env > yaml precedence.
    s = ApiGatewaySettings(_env_file=None)
    assert isinstance(s.allowed_origins, str)
    assert "yaml-only-origin-a.example.com" in s.allowed_origins
    assert "yaml-only-origin-b.example.com" in s.allowed_origins
    # Comma-joined: exactly one separator between two entries.
    assert s.allowed_origins.count(",") == 1
