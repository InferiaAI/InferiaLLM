"""Tests for UnifiedBaseSettings + the env > yaml > defaults precedence chain."""
import os
from typing import ClassVar, Optional
import pytest

from inferia.common.unified_config import UnifiedBaseSettings
from inferia.common.unified_config.loader import _clear_cache


class _Demo(UnifiedBaseSettings):
    _yaml_path: ClassVar[Optional[str]] = "services.api_gateway"
    port: int = 9999          # default; yaml says 8000
    jwt_secret_key: Optional[str] = None
    redis_host: str = "no.where"


def test_no_yaml_no_env_uses_defaults(tmp_path, monkeypatch, clean_env):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "inferia.common.unified_config.loader._SYSTEM_PATH",
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
    assert s.port == 8000   # yaml beats default
    assert s.redis_host == "localhost"


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
