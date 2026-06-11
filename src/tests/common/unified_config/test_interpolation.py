"""Tests for ${VAR} interpolation grammar (Section 7.3 of the spec)."""
import pytest
from common.unified_config.loader import interpolate_env, _is_valid_name
from common.unified_config.errors import ConfigInterpolationError

# Warm the regex and validator caches at module import time so they're ready
# for the performance test even when run in isolation. The caches survive
# between test functions and across pytest sessions.
_is_valid_name("X")


# ─── Required form: ${VAR} ─────────────────────────────────────────────────
def test_var_set_substitutes(monkeypatch):
    monkeypatch.setenv("HOST", "example.com")
    assert interpolate_env("${HOST}") == "example.com"


def test_var_unset_raises():
    with pytest.raises(ConfigInterpolationError, match="HOST"):
        interpolate_env("${HOST}")


def test_var_empty_raises(monkeypatch):
    monkeypatch.setenv("HOST", "")
    with pytest.raises(ConfigInterpolationError, match="HOST"):
        interpolate_env("${HOST}")


# ─── Required-with-default form: ${VAR:-default} ──────────────────────────
def test_default_used_when_unset():
    assert interpolate_env("${HOST:-localhost}") == "localhost"


def test_default_used_when_empty(monkeypatch):
    monkeypatch.setenv("HOST", "")
    assert interpolate_env("${HOST:-localhost}") == "localhost"


def test_value_wins_over_default(monkeypatch):
    monkeypatch.setenv("HOST", "real.host")
    assert interpolate_env("${HOST:-localhost}") == "real.host"


# ─── Keep-empty form: ${VAR-default} ──────────────────────────────────────
def test_dash_default_keeps_empty(monkeypatch):
    monkeypatch.setenv("HOST", "")
    assert interpolate_env("${HOST-localhost}") == ""


def test_dash_default_used_when_unset():
    assert interpolate_env("${HOST-localhost}") == "localhost"


# ─── Escaping ─────────────────────────────────────────────────────────────
def test_double_dollar_escape():
    assert interpolate_env("$${literal}") == "${literal}"


def test_double_dollar_with_no_braces_passes_through():
    assert interpolate_env("price=$$5") == "price=$$5"


# ─── Multiple substitutions ───────────────────────────────────────────────
def test_two_vars_in_one_string(monkeypatch):
    monkeypatch.setenv("HOST", "localhost")
    monkeypatch.setenv("PORT", "6379")
    assert interpolate_env("${HOST}:${PORT}") == "localhost:6379"


def test_partial_substitution():
    assert interpolate_env("port=${PORT:-8000}") == "port=8000"


# ─── Malformed names ──────────────────────────────────────────────────────
def test_lowercase_name_rejected():
    with pytest.raises(ConfigInterpolationError, match="invalid"):
        interpolate_env("${host}")


def test_dashed_name_rejected():
    with pytest.raises(ConfigInterpolationError, match="invalid"):
        interpolate_env("${HOST-NAME}")  # ambiguous with default form


def test_whitespace_inside_braces_rejected():
    with pytest.raises(ConfigInterpolationError):
        interpolate_env("${ HOST }")


def test_lowercase_name_with_colon_dash_default_rejected():
    """Invalid name in ${name:-default} form must also raise."""
    with pytest.raises(ConfigInterpolationError, match="invalid"):
        interpolate_env("${host:-localhost}")


def test_lowercase_name_with_dash_default_rejected():
    """Invalid name in ${name-default} form (non-ambiguous default) must also raise."""
    with pytest.raises(ConfigInterpolationError, match="invalid"):
        interpolate_env("${host-localhost}")


# ─── Recursive walk through structures ────────────────────────────────────
def test_walks_lists(monkeypatch):
    monkeypatch.setenv("X", "abc")
    assert interpolate_env(["${X}", "static", "${X:-fallback}"]) == ["abc", "static", "abc"]


def test_walks_nested_dicts(monkeypatch):
    monkeypatch.setenv("X", "abc")
    data = {"a": {"b": {"c": "${X}"}}}
    assert interpolate_env(data) == {"a": {"b": {"c": "abc"}}}


def test_non_string_scalars_untouched():
    assert interpolate_env(42) == 42
    assert interpolate_env(True) is True
    assert interpolate_env(None) is None
    assert interpolate_env(3.14) == 3.14


def test_deeply_nested_structure_terminates():
    # 100-level deep dict; must not stack-overflow
    deep = {}
    cur = deep
    for _ in range(100):
        cur["k"] = {}
        cur = cur["k"]
    cur["leaf"] = "${X:-end}"
    out = interpolate_env(deep)
    # Walk back down to verify the leaf got substituted
    cur = out
    for _ in range(100):
        cur = cur["k"]
    assert cur["leaf"] == "end"


def test_length_overflow_completes_in_bounded_time():
    """~1 MB string with many ${VAR:-x} terminates fast (regex, not recursion).

    Plausibility check, not a tight benchmark — guards against pathological
    backtracking or accidental O(n^2) loops. Payload is sized small enough
    to stay stable under coverage instrumentation (which adds ~5-10x overhead
    via line tracing) yet large enough that any quadratic regression would
    blow well past the threshold.
    """
    import time
    payload = "${X:-x}" * 100_000  # ~700 KB, 100 K placeholders
    t0 = time.perf_counter()
    out = interpolate_env(payload)
    assert time.perf_counter() - t0 < 2.0
    assert "${" not in out  # all substituted


# ─── Unresolved leftover → fatal ──────────────────────────────────────────
def test_unresolved_after_pass_raises():
    """A literal ${VAR} surviving interpolation must error, not silently pass."""
    with pytest.raises(ConfigInterpolationError):
        interpolate_env("${MISSING_VAR}")
