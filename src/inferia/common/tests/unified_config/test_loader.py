"""Tests for find_config_path and load_yaml (Sections 7.1 and 7.2 of the spec)."""
import pytest
from pathlib import Path

from inferia.common.unified_config.loader import find_config_path, load_yaml
from inferia.common.unified_config.errors import (
    ConfigNotFoundError,
    ConfigParseError,
)


# ─── find_config_path: explicit sources ───────────────────────────────────
def test_explicit_path_argument_used(fixtures_dir, clean_env):
    p = fixtures_dir / "valid.yaml"
    assert find_config_path(explicit=str(p)) == p


def test_explicit_path_missing_raises(clean_env, tmp_path):
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigNotFoundError, match="nope.yaml"):
        find_config_path(explicit=str(missing))


def test_env_var_used_when_no_explicit(fixtures_dir, monkeypatch, clean_env):
    p = fixtures_dir / "valid.yaml"
    monkeypatch.setenv("INFERIA_CONFIG", str(p))
    assert find_config_path() == p


def test_env_var_missing_raises(monkeypatch, clean_env, tmp_path):
    missing = tmp_path / "nope.yaml"
    monkeypatch.setenv("INFERIA_CONFIG", str(missing))
    with pytest.raises(ConfigNotFoundError):
        find_config_path()


def test_explicit_overrides_env_var(fixtures_dir, monkeypatch, clean_env):
    p1 = fixtures_dir / "valid.yaml"
    p2 = fixtures_dir / "minimal.yaml"
    monkeypatch.setenv("INFERIA_CONFIG", str(p1))
    assert find_config_path(explicit=str(p2)) == p2


# ─── find_config_path: implicit cwd + /etc fallback ───────────────────────
def test_cwd_yaml_discovered(tmp_path, monkeypatch, clean_env):
    target = tmp_path / "inferia.yaml"
    target.write_text("version: 1\n")
    monkeypatch.chdir(tmp_path)
    assert find_config_path() == target


def test_etc_yaml_discovered(tmp_path, monkeypatch, clean_env):
    # Point the "system" search path at tmp_path/etc/inferia/inferia.yaml
    etc = tmp_path / "etc" / "inferia"
    etc.mkdir(parents=True)
    target = etc / "inferia.yaml"
    target.write_text("version: 1\n")
    monkeypatch.setattr(
        "inferia.common.unified_config.loader._SYSTEM_PATH",
        target,
    )
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert find_config_path() == target


def test_none_when_nothing_found(tmp_path, monkeypatch, clean_env):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "inferia.common.unified_config.loader._SYSTEM_PATH",
        tmp_path / "nonexistent.yaml",
    )
    assert find_config_path() is None


# ─── load_yaml ───────────────────────────────────────────────────────────
def test_load_valid_yaml_returns_dict(fixtures_dir):
    data = load_yaml(fixtures_dir / "valid.yaml")
    assert isinstance(data, dict)
    assert data["version"] == 1
    assert data["services"]["api_gateway"]["enabled"] is True


def test_load_empty_yaml_returns_empty_dict(fixtures_dir):
    """yaml.safe_load('') returns None — we must coerce to {}."""
    assert load_yaml(fixtures_dir / "empty.yaml") == {}


def test_load_bad_syntax_raises_with_filename_in_message(fixtures_dir):
    """Filename must appear in the error so users can locate the bad file."""
    with pytest.raises(ConfigParseError, match="bad_syntax.yaml"):
        load_yaml(fixtures_dir / "bad_syntax.yaml")


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(ConfigNotFoundError):
        load_yaml(tmp_path / "nope.yaml")


def test_load_non_mapping_top_level_raises(fixtures_dir):
    """A top-level YAML list or scalar is not a valid config — reject early."""
    with pytest.raises(ConfigParseError, match="must be a mapping"):
        load_yaml(fixtures_dir / "list_top_level.yaml")


# ─── validate_schema ──────────────────────────────────────────────────────
from inferia.common.unified_config.loader import (
    validate_schema,
    load_unified_config,
    _clear_cache,
)
from inferia.common.unified_config.errors import (
    ConfigValidationError,
    ConfigInterpolationError,
)
from inferia.common.unified_config.schema import InferiaConfig


def test_validate_schema_returns_inferia_config():
    cfg = validate_schema({"version": 1, "environment": "development"})
    assert isinstance(cfg, InferiaConfig)
    assert cfg.environment == "development"


def test_validate_schema_wraps_validation_error():
    with pytest.raises(ConfigValidationError, match="version"):
        validate_schema({})


# ─── load_unified_config orchestrator ─────────────────────────────────────
def test_load_unified_config_returns_none_when_no_yaml(tmp_path, monkeypatch, clean_env):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "inferia.common.unified_config.loader._SYSTEM_PATH",
        tmp_path / "missing.yaml",
    )
    _clear_cache()
    assert load_unified_config() is None


def test_load_unified_config_full_path(fixtures_dir, clean_env):
    _clear_cache()
    cfg = load_unified_config(path=str(fixtures_dir / "valid.yaml"))
    assert isinstance(cfg, InferiaConfig)
    assert cfg.services.api_gateway.enabled is True


def test_load_unified_config_interpolation_failure(fixtures_dir, clean_env, monkeypatch):
    monkeypatch.delenv("SOME_UNSET_REQUIRED_SECRET", raising=False)
    _clear_cache()
    with pytest.raises(ConfigInterpolationError):
        load_unified_config(path=str(fixtures_dir / "unresolved_var.yaml"))


def test_load_unified_config_caches_per_path(fixtures_dir, clean_env):
    _clear_cache()
    a = load_unified_config(path=str(fixtures_dir / "valid.yaml"))
    b = load_unified_config(path=str(fixtures_dir / "valid.yaml"))
    assert a is b


def test_load_unified_config_cache_keyed_by_path(fixtures_dir, clean_env):
    _clear_cache()
    a = load_unified_config(path=str(fixtures_dir / "valid.yaml"))
    b = load_unified_config(path=str(fixtures_dir / "minimal.yaml"))
    assert a is not b
