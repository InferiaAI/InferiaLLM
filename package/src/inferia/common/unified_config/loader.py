"""Unified config loader — Phase 1.

This module is pure-function and import-light. It does NOT touch
pydantic-settings; the source/base classes do that.
"""
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .errors import (
    ConfigInterpolationError,
    ConfigNotFoundError,
    ConfigParseError,
)


# Matches either:
#   \$\$\{   — escape sequence  "$${"  (group 1 is None)
#   \$\{…\}  — placeholder      "${…}" (group 1 is body inside braces)
_PLACEHOLDER_RE = re.compile(r"\$\$\{|\$\{([^}]*)\}")

# Valid env var name: starts with letter/underscore, all upper/digit/underscore.
_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


@lru_cache(maxsize=512)
def _is_valid_name(name: str) -> bool:
    """Return True if *name* is a legal env var name (cached for hot paths).

    The lru_cache turns O(n_placeholders) regex matches into O(n_unique_names)
    for nested config walks where the same variable name appears many times.
    """
    return bool(_NAME_RE.match(name))


def _resolve(body: str, env: dict[str, str]) -> str:
    """Resolve a single placeholder body to its substituted value.

    Bodies take three forms:
      NAME            — required: env value (set + nonempty); else raises
      NAME:-default   — env value (set + nonempty); else 'default'
      NAME-default    — env value (set, even if empty); else 'default'

    Stricter-than-POSIX safety net: if `body` is `NAME-DEFAULT` and `DEFAULT`
    itself matches the env-var name pattern (e.g. ${HOST-NAME}), we treat this
    as a likely typo for `${HOST_NAME}` and raise ConfigInterpolationError.
    Users who really want a literal default that looks like an env name can
    use the unambiguous ${NAME:-DEFAULT} form instead.
    """
    # Order matters: check ':-' before bare '-' so 'A:-b' is never mis-parsed.
    if ":-" in body:
        name, _, default = body.partition(":-")
        if not _is_valid_name(name):
            raise ConfigInterpolationError(
                f"invalid variable name: '{name}' in '${{{body}}}'"
            )
        val = env.get(name, "")
        return val if val else default

    if "-" in body:
        # ${NAME-default}: keep empty string, use default only when unset.
        # Stricter-than-POSIX safety net: reject if 'default' itself looks like
        # a bare env-var name — e.g. ${HOST-NAME} is almost certainly a typo
        # for ${HOST_NAME}. Use ${HOST:-NAME} to pass a literal env-var-shaped
        # default unambiguously.
        name, _, default = body.partition("-")
        if not _is_valid_name(name):
            raise ConfigInterpolationError(
                f"invalid variable name: '{name}' in '${{{body}}}'"
            )
        if _is_valid_name(default):
            # Ambiguous: ${FOO-BAR} looks like a dashed variable name (typo guard).
            raise ConfigInterpolationError(
                f"invalid variable name: '{body}' — use ':-' for a default value "
                f"or check for a typo in '${{{body}}}'"
            )
        return env[name] if name in env else default

    # Bare ${NAME} — required, must be set and non-empty.
    name = body
    if not _is_valid_name(name):
        raise ConfigInterpolationError(f"invalid variable name: '{name}'")
    val = env.get(name)
    if not val:
        raise ConfigInterpolationError(
            f"required environment variable '{name}' is unset or empty"
        )
    return val


def _interpolate_str(s: str, env: dict[str, str] | None = None) -> str:
    """Substitute every ${VAR} placeholder in a single string.

    Uses a single-pass tokenizer via re.finditer — no sentinel, no post-scan.
    Takes a snapshot of os.environ once per call for performance on large inputs.
    Variable name validation results are cached via lru_cache, making repeated
    lookups of the same name O(1) — important for high-repetition payloads.
    """
    # Snapshot os.environ once — plain dict lookup is ~10x faster than
    # os.environ.get() for high-repetition workloads (e.g. 10 MB payloads).
    if env is None:
        env = dict(os.environ)

    parts: list[str] = []
    prev_end = 0
    for m in _PLACEHOLDER_RE.finditer(s):
        # Emit the literal chunk before this match.
        parts.append(s[prev_end : m.start()])
        if m.group(1) is None:
            # Matched "$${"  — emit a literal "${" (escape sequence).
            parts.append("${")
        else:
            # Matched "${body}" — resolve it (raises on error).
            parts.append(_resolve(m.group(1), env))
        prev_end = m.end()
    # Emit the tail after the last match (or the whole string if no matches).
    parts.append(s[prev_end:])
    return "".join(parts)


def interpolate_env(obj: Any) -> Any:
    """Recursively substitute ${VAR} placeholders in a yaml-decoded structure.

    Handles the value shapes `yaml.safe_load` returns: str / int / float /
    bool / None / list / dict. String scalars are substituted; other scalars
    pass through; lists and dicts are walked (their *contents* substituted,
    not their keys). Tuples, sets, and custom classes are returned unchanged.
    """
    if isinstance(obj, str):
        return _interpolate_str(obj)
    if isinstance(obj, dict):
        return {k: interpolate_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [interpolate_env(v) for v in obj]
    return obj


# System-wide fallback path. Overridable in tests via monkeypatch.
_SYSTEM_PATH = Path("/etc/inferia/inferia.yaml")


def find_config_path(explicit: str | None = None) -> Path | None:
    """Discover the unified config file.

    Resolution order (Section 7.1 of the spec):
      1. `explicit` argument (e.g. --config flag value)
      2. $INFERIA_CONFIG env var
      3. ./inferia.yaml in current working dir
      4. /etc/inferia/inferia.yaml
      5. None  (no yaml; caller should fall back to env+defaults)

    Cases 1 and 2 are explicit — a missing file raises ConfigNotFoundError.
    Cases 3 and 4 are implicit — a missing file moves on to the next.
    """
    if explicit is not None:
        p = Path(explicit)
        if not p.exists():
            raise ConfigNotFoundError(f"config file not found: {p}")
        return p

    env_path = os.environ.get("INFERIA_CONFIG")
    if env_path:
        p = Path(env_path)
        if not p.exists():
            raise ConfigNotFoundError(
                f"INFERIA_CONFIG points to non-existent path: {p}"
            )
        return p

    cwd_path = Path.cwd() / "inferia.yaml"
    if cwd_path.exists():
        return cwd_path

    if _SYSTEM_PATH.exists():
        return _SYSTEM_PATH

    return None


def load_yaml(path: Path | str) -> dict:
    """Load and parse a yaml file.

    Empty file → {} (yaml.safe_load returns None for empty input; we coerce).
    Non-mapping top-level (e.g. a YAML list or bare scalar) → ConfigParseError.
    Bad syntax → ConfigParseError (wraps yaml.YAMLError with file context).
    Missing file → ConfigNotFoundError.
    """
    p = Path(path)
    if not p.exists():
        raise ConfigNotFoundError(f"config file not found: {p}")
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigParseError(f"failed to parse {p}: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigParseError(
            f"{p}: top-level YAML must be a mapping, got {type(data).__name__}"
        )
    return data
