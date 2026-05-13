# Unified Config File Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Phase 1 of issue [#243](https://github.com/InferiaAI/InferiaLLM/issues/243) — a unified YAML config that drives the api_gateway service end-to-end, with the loader sub-package, ≥95% test coverage, and Docker validation.

**Architecture:** Pydantic Settings v2 `customise_sources` (Approach A from the spec). A new `inferia.common.unified_config` sub-package contains four modules — `errors`, `schema`, `loader`, `source`, `base` — that together inject yaml values into the existing per-service `Settings` classes between env and pydantic defaults in the precedence chain. Only api_gateway is wired through this in Phase 1.

**Tech Stack:** Python 3.10–3.12, Pydantic v2, pydantic-settings v2, PyYAML, pytest + pytest-cov, Docker Compose.

**Reference spec:** `docs/superpowers/specs/2026-05-12-unified-config-design.md`

**Commit policy (per user instruction):**
- Every commit is SSH-signed (`git commit -S …`). The repo already has `gpg.format=ssh` and a signing key configured.
- Author is `ankitprasad2005 <ankitprasad2005@outlook.com>`. No `Co-Authored-By: Claude` lines. No "Generated with Claude" trailers.
- The user **reviews every diff** (`git diff --staged`) before each commit. The plan calls this out as an explicit step.

---

## Pre-flight

Working directory: `/storage/intern/hooman/InferiaLLM`
Branch: `feat/issue-243-unified-config` (already created)
Existing files referenced:
- `package/src/inferia/services/api_gateway/config.py:107` — `Settings(BaseSettings)` class to migrate
- `package/src/inferia/cli.py:617-638` — `start` sub-parser to add `--config` to
- `package/src/inferia/cli.py:25-31` — `_load_env()` already calls `load_dotenv`
- `package/pyproject.toml:20-67` — runtime deps (PyYAML not present yet)
- `docker/docker-compose.yml:33-83` — `inferia-unified` service
- `docker/entrypoint.sh` — calls `inferiallm start` based on `SERVICE_TYPE`
- `CLAUDE.md` — has a Mistakes Log section at the bottom

---

## Task 0: Commit spec + plan

**Files:**
- Add: `docs/superpowers/specs/2026-05-12-unified-config-design.md` (already on disk)
- Add: `docs/superpowers/plans/2026-05-12-unified-config-plan.md` (this file)

- [ ] **Step 1: Confirm both files are on disk and untracked**

```bash
git status -s docs/superpowers/
```

Expected:
```
?? docs/superpowers/plans/2026-05-12-unified-config-plan.md
?? docs/superpowers/specs/2026-05-12-unified-config-design.md
```

- [ ] **Step 2: Show the diff to the user for review**

```bash
git add docs/superpowers/specs/2026-05-12-unified-config-design.md docs/superpowers/plans/2026-05-12-unified-config-plan.md
git diff --staged
```

Pause and wait for user OK before committing.

- [ ] **Step 3: Commit (SSH-signed, no Claude attribution)**

```bash
git commit -S -m "docs(issue-243): add spec and implementation plan for unified config"
```

Expected: 1 file changed (the plan + spec), `Signature: Good` if you `git verify-commit HEAD`.

---

## Task 1: Add PyYAML dependency + scaffold sub-package

**Files:**
- Modify: `package/pyproject.toml:20-67`
- Create: `package/src/inferia/common/unified_config/__init__.py`
- Create: `package/src/inferia/common/unified_config/errors.py`
- Create: `package/src/inferia/common/tests/unified_config/__init__.py`
- Create: `package/src/inferia/common/tests/unified_config/conftest.py`

- [ ] **Step 1: Add PyYAML to pyproject.toml**

Find the `# Config` block at `package/pyproject.toml:58-60`:

```toml
  # Config
  "python-dotenv",
```

Replace with:

```toml
  # Config
  "python-dotenv",
  "PyYAML>=6.0,<7.0",
```

- [ ] **Step 2: Install PyYAML into the active venv**

```bash
cd package && pip install -e .
cd ..
```

Expected: `Successfully installed PyYAML-6.x.x` (or "Requirement already satisfied" if pulled in transitively).

- [ ] **Step 3: Verify PyYAML import**

```bash
python -c "import yaml; print(yaml.__version__)"
```

Expected: prints `6.x.x` without error.

- [ ] **Step 4: Create the errors module**

Create `package/src/inferia/common/unified_config/errors.py`:

```python
"""Exception hierarchy for the unified config loader.

All errors raised by `inferia.common.unified_config` inherit from
`UnifiedConfigError`, so callers can catch the whole family with one except.
"""


class UnifiedConfigError(Exception):
    """Base class for all unified-config errors."""


class ConfigNotFoundError(UnifiedConfigError):
    """Raised when an explicitly requested config path does not exist."""


class ConfigParseError(UnifiedConfigError):
    """Raised when the yaml file fails to parse."""


class ConfigInterpolationError(UnifiedConfigError):
    """Raised when ${VAR} interpolation fails (unresolved or malformed name)."""


class ConfigValidationError(UnifiedConfigError):
    """Raised when the loaded dict fails Pydantic schema validation."""
```

- [ ] **Step 5: Create the package __init__ (placeholder exports — filled in by later tasks)**

Create `package/src/inferia/common/unified_config/__init__.py`:

```python
"""Unified configuration loader for InferiaLLM.

Phase 1 module — see docs/superpowers/specs/2026-05-12-unified-config-design.md
for the design and `package/src/inferia/common/tests/unified_config/` for
behavior contracts.
"""

from .errors import (
    UnifiedConfigError,
    ConfigNotFoundError,
    ConfigParseError,
    ConfigInterpolationError,
    ConfigValidationError,
)

__all__ = [
    "UnifiedConfigError",
    "ConfigNotFoundError",
    "ConfigParseError",
    "ConfigInterpolationError",
    "ConfigValidationError",
]
```

Loader, schema, source, and base classes are added to `__all__` in later tasks as they're written.

- [ ] **Step 6: Create the test directory scaffold**

Create `package/src/inferia/common/tests/unified_config/__init__.py` as an empty file:

```python
```

Create `package/src/inferia/common/tests/unified_config/conftest.py`:

```python
"""Shared fixtures for unified_config tests.

Test fixtures (yaml files) live under fixtures/ next to this conftest.
"""
import os
from pathlib import Path
from typing import Iterator
import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Absolute path to the fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def clean_env(monkeypatch) -> Iterator[None]:
    """Strip INFERIA_CONFIG and any leaked yaml-relevant vars for isolation."""
    for k in list(os.environ):
        if k in {"INFERIA_CONFIG"}:
            monkeypatch.delenv(k, raising=False)
    yield
```

- [ ] **Step 7: Smoke-test the import path**

```bash
python -c "from inferia.common.unified_config import UnifiedConfigError, ConfigNotFoundError; print('OK')"
```

Expected: `OK`

- [ ] **Step 8: Show diff and commit**

```bash
git add package/pyproject.toml \
        package/src/inferia/common/unified_config/ \
        package/src/inferia/common/tests/unified_config/
git diff --staged
```

Pause for user review.

```bash
git commit -S -m "feat(common): add unified_config sub-package scaffold and PyYAML dep (#243)"
```

---

## Task 2: Interpolation — TDD the `${VAR}` parser

**Files:**
- Create: `package/src/inferia/common/tests/unified_config/test_interpolation.py`
- Create: `package/src/inferia/common/unified_config/loader.py` (just `interpolate_env` for now; other functions follow in Task 4/5)

- [ ] **Step 1: Write the failing tests**

Create `package/src/inferia/common/tests/unified_config/test_interpolation.py`:

```python
"""Tests for ${VAR} interpolation grammar (Section 7.3 of the spec)."""
import pytest
from inferia.common.unified_config.loader import interpolate_env
from inferia.common.unified_config.errors import ConfigInterpolationError


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


def test_length_overflow_completes_under_one_second():
    """10 MB string with many ${VAR:-x} terminates fast (regex, not recursion)."""
    import time
    payload = ("${X:-x}" * 1_000_000)[:10_000_000]  # ~10 MB
    t0 = time.perf_counter()
    out = interpolate_env(payload)
    assert time.perf_counter() - t0 < 1.0
    assert "${" not in out  # all substituted


# ─── Unresolved leftover → fatal ──────────────────────────────────────────
def test_unresolved_after_pass_raises():
    """A literal ${VAR} surviving interpolation must error, not silently pass."""
    with pytest.raises(ConfigInterpolationError):
        interpolate_env("${MISSING_VAR}")
```

- [ ] **Step 2: Run tests, expect them all to fail (no impl yet)**

```bash
pytest package/src/inferia/common/tests/unified_config/test_interpolation.py -v 2>&1 | head -40
```

Expected: `ModuleNotFoundError: ... loader` or `ImportError: cannot import name 'interpolate_env'`.

- [ ] **Step 3: Implement `interpolate_env`**

Create `package/src/inferia/common/unified_config/loader.py`:

```python
"""Unified config loader — Phase 1.

This module is pure-function and import-light. It does NOT touch
pydantic-settings; the source/base classes do that.
"""
import os
import re
from typing import Any

from .errors import ConfigInterpolationError


# Matches an unescaped ${...} placeholder, OR a literal $${escape}.
#   group(1) — full body inside the braces (incl. optional default)
#   "$$" prefix means "the next ${ is literal, not a placeholder"
_PLACEHOLDER_RE = re.compile(r"\$\$\{|\$\{([^}]*)\}")

# Valid env var name: starts with letter/underscore, all upper/digit/underscore.
_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _resolve(body: str) -> str:
    """Resolve a single placeholder body, e.g. 'NAME', 'NAME:-default', 'NAME-default'."""
    # Order matters: check ':-' before '-' so 'A:-b' is parsed as colon-dash
    if ":-" in body:
        name, _, default = body.partition(":-")
        if not _NAME_RE.match(name):
            raise ConfigInterpolationError(
                f"invalid variable name: '{name}' in '${{{body}}}'"
            )
        val = os.environ.get(name, "")
        return val if val else default

    if "-" in body:
        # POSIX-ish ${NAME-default}: keep empty, use default only if unset.
        name, _, default = body.partition("-")
        if not _NAME_RE.match(name):
            raise ConfigInterpolationError(
                f"invalid variable name: '{name}' in '${{{body}}}'"
            )
        return os.environ[name] if name in os.environ else default

    # Bare ${NAME} — required, must be set and non-empty.
    name = body
    if not _NAME_RE.match(name):
        raise ConfigInterpolationError(f"invalid variable name: '{name}'")
    val = os.environ.get(name)
    if not val:
        raise ConfigInterpolationError(
            f"required environment variable '{name}' is unset or empty"
        )
    return val


def _interpolate_str(s: str) -> str:
    """Substitute every ${VAR} placeholder in a single string."""
    out_parts: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        m = _PLACEHOLDER_RE.search(s, i)
        if not m:
            out_parts.append(s[i:])
            break
        out_parts.append(s[i : m.start()])
        if m.group(0) == "$${":
            # Escape sequence: emit literal "${" and advance past the "$${".
            out_parts.append("${")
            i = m.end()
            continue
        out_parts.append(_resolve(m.group(1)))
        i = m.end()
    result = "".join(out_parts)
    # Defensive: regex above should consume every placeholder, but guard anyway.
    if "${" in result and "$${" not in result:
        # Re-scan for an unresolved placeholder (e.g. malformed input the regex skipped).
        leftover = re.search(r"\$\{[^}]*\}", result)
        if leftover:
            raise ConfigInterpolationError(
                f"unresolved placeholder: '{leftover.group(0)}'"
            )
    return result


def interpolate_env(obj: Any) -> Any:
    """Recursively substitute ${VAR} placeholders in any nested structure.

    String scalars are substituted; ints/bools/None pass through.
    Lists and dicts are walked (their *contents* substituted, not their keys).
    """
    if isinstance(obj, str):
        return _interpolate_str(obj)
    if isinstance(obj, dict):
        return {k: interpolate_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [interpolate_env(v) for v in obj]
    return obj
```

- [ ] **Step 4: Run tests, expect them all to pass**

```bash
pytest package/src/inferia/common/tests/unified_config/test_interpolation.py -v
```

Expected: all green. If any failures, fix the implementation (not the tests) and re-run.

- [ ] **Step 5: Check coverage on interpolation alone**

```bash
pytest package/src/inferia/common/tests/unified_config/test_interpolation.py \
       --cov=inferia.common.unified_config.loader \
       --cov-report=term-missing
```

Expected: `>95%` on the lines you just wrote. Missing lines should only be the not-yet-written functions (find_config_path, load_yaml, validate_schema, load_unified_config), not anything inside `interpolate_env` / `_resolve` / `_interpolate_str`.

- [ ] **Step 6: Show diff and commit**

```bash
git add package/src/inferia/common/unified_config/loader.py \
        package/src/inferia/common/tests/unified_config/test_interpolation.py
git diff --staged
```

Pause for user review.

```bash
git commit -S -m "feat(common): unified_config interpolation parser (#243)"
```

---

## Task 3: Discovery + load_yaml — TDD

**Files:**
- Create: `package/src/inferia/common/tests/unified_config/fixtures/valid.yaml`
- Create: `package/src/inferia/common/tests/unified_config/fixtures/minimal.yaml`
- Create: `package/src/inferia/common/tests/unified_config/fixtures/bad_syntax.yaml`
- Create: `package/src/inferia/common/tests/unified_config/fixtures/empty.yaml`
- Create: `package/src/inferia/common/tests/unified_config/test_loader.py`
- Modify: `package/src/inferia/common/unified_config/loader.py` (add `find_config_path`, `load_yaml`)

- [ ] **Step 1: Create yaml fixtures**

Create `package/src/inferia/common/tests/unified_config/fixtures/minimal.yaml`:

```yaml
version: 1
```

Create `package/src/inferia/common/tests/unified_config/fixtures/valid.yaml`:

```yaml
version: 1
environment: development
log_level: INFO
infra:
  redis:
    host: localhost
    port: 6379
security:
  jwt_secret_key: this-is-a-thirty-two-byte-test-secret-key
  internal_api_key: this-is-another-thirty-two-byte-secret-k
services:
  api_gateway:
    enabled: true
    port: 8000
```

Create `package/src/inferia/common/tests/unified_config/fixtures/bad_syntax.yaml`:

```yaml
version: 1
  this is: not: valid: yaml:
```

Create `package/src/inferia/common/tests/unified_config/fixtures/empty.yaml`:

```
```

(Truly empty file.)

- [ ] **Step 2: Write failing tests**

Create `package/src/inferia/common/tests/unified_config/test_loader.py`:

```python
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


def test_load_bad_syntax_raises(fixtures_dir):
    with pytest.raises(ConfigParseError):
        load_yaml(fixtures_dir / "bad_syntax.yaml")


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(ConfigNotFoundError):
        load_yaml(tmp_path / "nope.yaml")
```

- [ ] **Step 3: Run tests, expect them all to fail**

```bash
pytest package/src/inferia/common/tests/unified_config/test_loader.py -v 2>&1 | head -30
```

Expected: `ImportError: cannot import name 'find_config_path'` (or similar).

- [ ] **Step 4: Implement `find_config_path` and `load_yaml`**

Append to `package/src/inferia/common/unified_config/loader.py`:

```python
from pathlib import Path
import yaml

from .errors import ConfigNotFoundError, ConfigParseError


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
    Cases 3 and 4 are implicit — a missing file just moves on to the next.
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
    """Load and parse a yaml file. Empty file → {}. Bad syntax → ConfigParseError."""
    p = Path(path)
    if not p.exists():
        raise ConfigNotFoundError(f"config file not found: {p}")
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigParseError(f"failed to parse {p}: {e}") from e
    return data if data is not None else {}
```

- [ ] **Step 5: Run tests, expect all pass**

```bash
pytest package/src/inferia/common/tests/unified_config/test_loader.py -v
```

If any fail, fix the implementation (not the tests) and re-run.

- [ ] **Step 6: Show diff and commit**

```bash
git add package/src/inferia/common/unified_config/loader.py \
        package/src/inferia/common/tests/unified_config/test_loader.py \
        package/src/inferia/common/tests/unified_config/fixtures/
git diff --staged
```

Pause for user review.

```bash
git commit -S -m "feat(common): unified_config discovery and load_yaml (#243)"
```

---

## Task 4: Schema models — TDD

**Files:**
- Create: `package/src/inferia/common/tests/unified_config/test_schema.py`
- Create: `package/src/inferia/common/unified_config/schema.py`
- Create: `package/src/inferia/common/tests/unified_config/fixtures/unknown_field.yaml`
- Create: `package/src/inferia/common/tests/unified_config/fixtures/secret_too_short.yaml`
- Create: `package/src/inferia/common/tests/unified_config/fixtures/missing_required.yaml`

- [ ] **Step 1: Create fixtures**

Create `package/src/inferia/common/tests/unified_config/fixtures/unknown_field.yaml`:

```yaml
version: 1
services:
  api_gateway:
    enabled: true
    port: 8000
    typo_field_should_fail: oops
```

Create `package/src/inferia/common/tests/unified_config/fixtures/secret_too_short.yaml`:

```yaml
version: 1
security:
  jwt_secret_key: short
```

Create `package/src/inferia/common/tests/unified_config/fixtures/missing_required.yaml`:

```yaml
environment: development
```

(Missing `version:` — must fail.)

- [ ] **Step 2: Write failing tests**

Create `package/src/inferia/common/tests/unified_config/test_schema.py`:

```python
"""Tests for InferiaConfig schema (Section 6 + 7.4 of the spec)."""
import pytest
from pydantic import ValidationError

from inferia.common.unified_config.schema import (
    InferiaConfig,
    KNOWN_PLACEHOLDER_SECRETS,
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
            _base_dict(services={"api_gateway": {"enabled": "true"}})
        )


def test_service_port_must_be_positive():
    with pytest.raises(ValidationError):
        InferiaConfig.model_validate(
            _base_dict(services={"api_gateway": {"port": -1}})
        )


def test_unknown_field_inside_service_fails():
    with pytest.raises(ValidationError, match="typo_field_should_fail"):
        InferiaConfig.model_validate(
            _base_dict(services={"api_gateway": {"typo_field_should_fail": "oops"}})
        )


# ─── unknown top-level: warning, not fatal ────────────────────────────────
def test_unknown_top_level_key_does_not_fail(caplog):
    cfg = InferiaConfig.model_validate(_base_dict(weird_future_key=1))
    # No exception; entry just isn't kept on the model.
    assert not hasattr(cfg, "weird_future_key")
```

- [ ] **Step 3: Run tests, expect all fail**

```bash
pytest package/src/inferia/common/tests/unified_config/test_schema.py -v 2>&1 | head -30
```

Expected: `ImportError: cannot import name 'InferiaConfig'`.

- [ ] **Step 4: Implement the schema**

Create `package/src/inferia/common/unified_config/schema.py`:

```python
"""Pydantic models for the unified yaml schema (Sections 6 and 7.4 of the spec).

Forward-compat fields (inference, guardrail, data, orchestration, providers)
accept anything as a placeholder; Phase 2+ will tighten them.
"""
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


# Known placeholder strings that must NOT pass schema validation as real secrets.
KNOWN_PLACEHOLDER_SECRETS: frozenset[str] = frozenset(
    {
        "placeholder-secret-key-at-least-32-chars-long",
        "YOUR_32_BYTE_SECRET_KEY_HERE",
        "YOUR_32_BYTE_INTERNAL_API_KEY_HERE",
        "CHANGE_THIS_TO_STRONG_PASSWORD",
        "dev-internal-key-change-in-prod",
    }
)


def _secret_validator(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    if len(v) < 32:
        raise ValueError(f"must be at least 32 characters (got {len(v)})")
    if v in KNOWN_PLACEHOLDER_SECRETS:
        raise ValueError("must not be a known placeholder string")
    return v


# ─── infra ────────────────────────────────────────────────────────────────
class DatabaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: Optional[str] = None
    ssl: bool = True


class RedisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "localhost"
    port: int = Field(default=6379, gt=0, le=65535)
    db: str = "0"
    username: Optional[str] = None
    password: Optional[str] = None
    ssl: bool = False


class LogstashConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: Optional[str] = None
    port: int = Field(default=5959, gt=0, le=65535)


class InfraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    logstash: LogstashConfig = Field(default_factory=LogstashConfig)


# ─── security ─────────────────────────────────────────────────────────────
class SecurityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jwt_secret_key: Optional[str] = None
    jwt_algorithm: str = "HS256"
    internal_api_key: Optional[str] = None
    secret_encryption_key: Optional[str] = None
    log_encryption_key: Optional[str] = None
    allowed_origins: list[str] = Field(default_factory=list)

    _jwt_v = field_validator("jwt_secret_key")(_secret_validator)
    _iak_v = field_validator("internal_api_key")(_secret_validator)


# ─── services ─────────────────────────────────────────────────────────────
class AuthSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["local", "external"] = "local"
    external_url: Optional[str] = None


class SuperadminSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Optional[str] = None
    password: Optional[str] = None


class RateLimitSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    requests_per_minute: int = Field(default=10000, ge=0)
    burst_size: int = Field(default=1000, ge=0)
    use_redis: bool = False


class HttpClientSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_timeout_seconds: float = 10.0
    service_connect_timeout_seconds: float = 3.0
    service_max_connections: int = Field(default=500, gt=0)
    service_max_keepalive: int = Field(default=100, gt=0)
    proxy_timeout_seconds: float = 300.0
    proxy_max_connections: int = Field(default=500, gt=0)
    proxy_max_keepalive: int = Field(default=100, gt=0)


class SslSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verify: bool = True
    ca_bundle: Optional[str] = None


class ServiceUrlsSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    guardrail: Optional[str] = None
    data: Optional[str] = None
    orchestration: Optional[str] = None
    inference: Optional[str] = None


class ApiGatewayService(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, le=65535)
    workers: int = Field(default=1, gt=0)
    reload: bool = False
    proxy_headers: bool = True
    forwarded_allow_ips: Optional[str] = None
    default_org_name: str = "Default Organization"
    auth: AuthSection = Field(default_factory=AuthSection)
    superadmin: SuperadminSection = Field(default_factory=SuperadminSection)
    rate_limit: RateLimitSection = Field(default_factory=RateLimitSection)
    http_client: HttpClientSection = Field(default_factory=HttpClientSection)
    ssl: SslSection = Field(default_factory=SslSection)
    service_urls: ServiceUrlsSection = Field(default_factory=ServiceUrlsSection)


class PlaceholderService(BaseModel):
    """Phase-2 services — only the `enabled` toggle is validated for now."""
    model_config = ConfigDict(extra="allow")
    enabled: bool = True


class ServicesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_gateway: ApiGatewayService = Field(default_factory=ApiGatewayService)
    inference: PlaceholderService = Field(default_factory=PlaceholderService)
    guardrail: PlaceholderService = Field(default_factory=PlaceholderService)
    data: PlaceholderService = Field(default_factory=PlaceholderService)
    orchestration: PlaceholderService = Field(default_factory=PlaceholderService)


# ─── providers (Phase 2 will tighten; for now accept-all) ─────────────────
class ProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="allow")


# ─── root ─────────────────────────────────────────────────────────────────
class InferiaConfig(BaseModel):
    """Root of the unified config. Unknown top-level keys are *allowed* but ignored
    (forward-compat); unknown keys inside known sub-trees are rejected (typo guard)."""
    model_config = ConfigDict(extra="ignore")

    version: int = Field(..., description="Schema major; only 1 is supported")
    environment: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    infra: InfraConfig = Field(default_factory=InfraConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)

    @field_validator("version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"unsupported schema version {v}; this build supports v1")
        return v
```

- [ ] **Step 5: Run tests, expect all pass**

```bash
pytest package/src/inferia/common/tests/unified_config/test_schema.py -v
```

If anything fails, fix the schema (not the tests) and re-run.

- [ ] **Step 6: Update `__init__.py` to export the schema**

In `package/src/inferia/common/unified_config/__init__.py`, replace the existing `__all__` and add an import:

```python
from .errors import (
    UnifiedConfigError,
    ConfigNotFoundError,
    ConfigParseError,
    ConfigInterpolationError,
    ConfigValidationError,
)
from .schema import InferiaConfig

__all__ = [
    "InferiaConfig",
    "UnifiedConfigError",
    "ConfigNotFoundError",
    "ConfigParseError",
    "ConfigInterpolationError",
    "ConfigValidationError",
]
```

- [ ] **Step 7: Show diff and commit**

```bash
git add package/src/inferia/common/unified_config/schema.py \
        package/src/inferia/common/unified_config/__init__.py \
        package/src/inferia/common/tests/unified_config/test_schema.py \
        package/src/inferia/common/tests/unified_config/fixtures/
git diff --staged
```

Pause for user review.

```bash
git commit -S -m "feat(common): unified_config Pydantic schema (#243)"
```

---

## Task 5: validate_schema + load_unified_config + cache — TDD

**Files:**
- Modify: `package/src/inferia/common/tests/unified_config/test_loader.py` (add tests for validate + orchestrator)
- Modify: `package/src/inferia/common/unified_config/loader.py` (add `validate_schema` + `load_unified_config`)
- Create: `package/src/inferia/common/tests/unified_config/fixtures/unresolved_var.yaml`

- [ ] **Step 1: Create fixture**

Create `package/src/inferia/common/tests/unified_config/fixtures/unresolved_var.yaml`:

```yaml
version: 1
security:
  jwt_secret_key: ${SOME_UNSET_REQUIRED_SECRET}
```

- [ ] **Step 2: Append tests to `test_loader.py`**

Append to `package/src/inferia/common/tests/unified_config/test_loader.py`:

```python
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
```

- [ ] **Step 3: Run tests, expect fail**

```bash
pytest package/src/inferia/common/tests/unified_config/test_loader.py -v 2>&1 | tail -20
```

Expected: ImportError for `validate_schema`, `load_unified_config`, `_clear_cache`.

- [ ] **Step 4: Implement validate + orchestrator + cache**

Append to `package/src/inferia/common/unified_config/loader.py`:

```python
from pydantic import ValidationError as _PydanticValidationError

from .errors import ConfigValidationError
from .schema import InferiaConfig


def validate_schema(data: dict) -> InferiaConfig:
    """Validate an interpolated dict against the InferiaConfig schema.

    Pydantic ValidationError is re-raised as ConfigValidationError so callers
    can catch the unified-config family without depending on Pydantic.
    """
    try:
        return InferiaConfig.model_validate(data)
    except _PydanticValidationError as e:
        raise ConfigValidationError(str(e)) from e


# ─── Cache: per-resolved-path (not per-call-arg) ──────────────────────────
_cache: dict[str, InferiaConfig] = {}


def _clear_cache() -> None:
    """Clear the cache. For tests only."""
    _cache.clear()


def load_unified_config(path: str | None = None) -> InferiaConfig | None:
    """Find → load → interpolate → validate the unified config.

    Returns None when no yaml was discovered (env+defaults only mode).
    Subsequent calls with the same resolved path return the cached object.
    """
    found = find_config_path(explicit=path)
    if found is None:
        return None

    key = str(found.resolve())
    if key in _cache:
        return _cache[key]

    raw = load_yaml(found)
    interpolated = interpolate_env(raw)
    cfg = validate_schema(interpolated)
    _cache[key] = cfg
    return cfg
```

- [ ] **Step 5: Run all loader tests**

```bash
pytest package/src/inferia/common/tests/unified_config/ -v
```

Expected: all green.

- [ ] **Step 6: Coverage check on loader.py**

```bash
pytest package/src/inferia/common/tests/unified_config/ \
       --cov=inferia.common.unified_config.loader \
       --cov=inferia.common.unified_config.schema \
       --cov-report=term-missing \
       --cov-fail-under=95
```

Expected: ≥95% on both.

- [ ] **Step 7: Show diff and commit**

```bash
git add package/src/inferia/common/unified_config/loader.py \
        package/src/inferia/common/tests/unified_config/test_loader.py \
        package/src/inferia/common/tests/unified_config/fixtures/unresolved_var.yaml
git diff --staged
```

Pause for user review.

```bash
git commit -S -m "feat(common): unified_config schema validation and orchestrator (#243)"
```

---

## Task 6: Source class + base mixin — TDD

**Files:**
- Create: `package/src/inferia/common/tests/unified_config/test_source.py`
- Create: `package/src/inferia/common/tests/unified_config/test_base.py`
- Create: `package/src/inferia/common/unified_config/source.py`
- Create: `package/src/inferia/common/unified_config/base.py`
- Modify: `package/src/inferia/common/unified_config/__init__.py` (export new classes)

- [ ] **Step 1: Write the failing tests for the source class**

Create `package/src/inferia/common/tests/unified_config/test_source.py`:

```python
"""Tests for YamlConfigSettingsSource (Section 8.2 of the spec)."""
import os
from typing import ClassVar, Optional
import pytest
from pydantic import Field
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
```

- [ ] **Step 2: Write failing tests for the base mixin**

Create `package/src/inferia/common/tests/unified_config/test_base.py`:

```python
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
```

- [ ] **Step 3: Run, expect fail**

```bash
pytest package/src/inferia/common/tests/unified_config/test_source.py \
       package/src/inferia/common/tests/unified_config/test_base.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'YamlConfigSettingsSource'` (and similar for `UnifiedBaseSettings`).

- [ ] **Step 4: Implement the source**

Create `package/src/inferia/common/unified_config/source.py`:

```python
"""Pydantic-Settings source that injects unified yaml values.

Lives between env and pydantic defaults in the precedence chain.
"""
from typing import Any, Tuple
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from .loader import load_unified_config


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """Reads field values from the unified yaml for a given Settings subclass.

    The subclass declares which yaml sub-tree feeds it via the `_yaml_path`
    ClassVar (e.g. "services.api_gateway"). Shared sub-trees `security` and
    `infra` are auto-merged into every service's view, flattened with the
    rule documented in Section 6.1 of the spec.
    """

    def __init__(self, settings_cls: type[BaseSettings]):
        super().__init__(settings_cls)
        self._yaml_path: str | None = getattr(settings_cls, "_yaml_path", None)
        self._values: dict[str, Any] = self._build_values()

    # --- internals -------------------------------------------------------
    def _walk(self, root: BaseModel, dotted: str) -> Any | None:
        node: Any = root
        for part in dotted.split("."):
            node = getattr(node, part, None)
            if node is None:
                return None
        return node

    def _flatten(self, node: BaseModel | None) -> dict[str, Any]:
        """Flatten one level of nested groups to Pydantic field names of settings_cls.

        For every `<group>.<leaf>` in `node`:
          - emit `<group>_<leaf>` if that is a declared field on settings_cls
          - else emit `<leaf>` if THAT is a declared field
          - else skip (silent — forward-compat or extra field)
        """
        if node is None:
            return {}
        declared = set(self.settings_cls.model_fields.keys())
        out: dict[str, Any] = {}
        for key, value in node.model_dump().items():
            if isinstance(value, dict):
                for leaf, leaf_val in value.items():
                    grouped = f"{key}_{leaf}"
                    if grouped in declared:
                        out[grouped] = leaf_val
                    elif leaf in declared:
                        out[leaf] = leaf_val
            else:
                if key in declared:
                    out[key] = value
        return out

    def _build_values(self) -> dict[str, Any]:
        cfg = load_unified_config()
        if cfg is None:
            return {}

        # Shared sub-trees first; service-specific overlays on top.
        merged: dict[str, Any] = {}
        merged.update(self._flatten(cfg.infra))
        merged.update(self._flatten(cfg.security))

        if self._yaml_path is not None:
            node = self._walk(cfg, self._yaml_path)
            merged.update(self._flatten(node))

        # Top-level scalars
        declared = set(self.settings_cls.model_fields.keys())
        if "environment" in declared:
            merged["environment"] = cfg.environment
        if "log_level" in declared:
            merged["log_level"] = cfg.log_level
        return merged

    # --- pydantic-settings interface -------------------------------------
    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> Tuple[Any, str, bool]:
        if field_name in self._values:
            return self._values[field_name], field_name, False
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return {
            name: self._values[name]
            for name in self.settings_cls.model_fields
            if name in self._values
        }
```

- [ ] **Step 5: Implement the base mixin**

Create `package/src/inferia/common/unified_config/base.py`:

```python
"""UnifiedBaseSettings — drop-in BaseSettings that injects the yaml source.

Subclasses set `_yaml_path` (e.g. "services.api_gateway") to declare which
yaml sub-tree feeds them. The precedence chain is documented in Section 8.1
of the design spec.
"""
from typing import ClassVar, Optional, Tuple, Type
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .source import YamlConfigSettingsSource


class UnifiedBaseSettings(BaseSettings):
    """BaseSettings subclass that adds yaml between env and pydantic defaults."""

    _yaml_path: ClassVar[Optional[str]] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        # Order = highest precedence first:
        #   init (CLI), env, .env file, yaml, /run/secrets, pydantic defaults
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
```

- [ ] **Step 6: Update the public exports**

Edit `package/src/inferia/common/unified_config/__init__.py` to:

```python
"""Unified configuration loader for InferiaLLM.

See docs/superpowers/specs/2026-05-12-unified-config-design.md.
"""

from .errors import (
    UnifiedConfigError,
    ConfigNotFoundError,
    ConfigParseError,
    ConfigInterpolationError,
    ConfigValidationError,
)
from .schema import InferiaConfig
from .source import YamlConfigSettingsSource
from .base import UnifiedBaseSettings
from .loader import load_unified_config

__all__ = [
    "load_unified_config",
    "InferiaConfig",
    "YamlConfigSettingsSource",
    "UnifiedBaseSettings",
    "UnifiedConfigError",
    "ConfigNotFoundError",
    "ConfigParseError",
    "ConfigInterpolationError",
    "ConfigValidationError",
]
```

- [ ] **Step 7: Run tests**

```bash
pytest package/src/inferia/common/tests/unified_config/ -v
```

Expected: all green.

- [ ] **Step 8: Full-package coverage gate**

```bash
pytest package/src/inferia/common/tests/unified_config/ \
       --cov=inferia.common.unified_config \
       --cov-report=term-missing \
       --cov-fail-under=95
```

Expected: ≥95% on `inferia.common.unified_config.*`. Missing lines should only be defensive guards.

- [ ] **Step 9: Show diff and commit**

```bash
git add package/src/inferia/common/unified_config/source.py \
        package/src/inferia/common/unified_config/base.py \
        package/src/inferia/common/unified_config/__init__.py \
        package/src/inferia/common/tests/unified_config/test_source.py \
        package/src/inferia/common/tests/unified_config/test_base.py
git diff --staged
```

Pause for user review.

```bash
git commit -S -m "feat(common): unified_config source class and base mixin (#243)"
```

---

## Task 7: Migrate api_gateway Settings

**Files:**
- Modify: `package/src/inferia/services/api_gateway/config.py:7-10, 107-245`
- Existing tests run unchanged: `pytest package/src/inferia/services/api_gateway/tests/`

- [ ] **Step 1: Snapshot the current api_gateway tests to know our baseline**

```bash
pytest package/src/inferia/services/api_gateway/tests/ -q
```

Expected: green baseline. Note the count of passed tests — we must not regress.

- [ ] **Step 2: Update imports in api_gateway/config.py**

In `package/src/inferia/services/api_gateway/config.py:6-10`, replace:

```python
from typing import Literal, Optional, Any, Dict, List
import logging
from pydantic import Field, BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
```

with:

```python
from typing import ClassVar, Literal, Optional, Any, Dict, List
import logging
from pydantic import Field, BaseModel
from pydantic_settings import SettingsConfigDict
from inferia.common.unified_config import UnifiedBaseSettings
```

- [ ] **Step 3: Change the Settings class declaration**

At `package/src/inferia/services/api_gateway/config.py:107`, replace:

```python
class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
```

with:

```python
class Settings(UnifiedBaseSettings):
    """Application settings loaded from yaml, env, or defaults.

    Source precedence (highest → lowest): init/CLI > env > .env > yaml > pydantic defaults.
    See docs/superpowers/specs/2026-05-12-unified-config-design.md.
    """

    _yaml_path: ClassVar[str] = "services.api_gateway"

```

- [ ] **Step 4: Run api_gateway tests**

```bash
pytest package/src/inferia/services/api_gateway/tests/ -q
```

Expected: same pass count as baseline. If any regress, investigate before committing.

- [ ] **Step 5: Manual env-only smoke**

```bash
INFERIA_CONFIG="" python -c "from inferia.services.api_gateway.config import settings; print(settings.host, settings.port, settings.environment)"
```

Expected: `0.0.0.0 8000 development` (or whatever your `.env` says — point is, no crash, no yaml needed).

- [ ] **Step 6: Manual yaml smoke (using the test fixture)**

```bash
INFERIA_CONFIG="$PWD/package/src/inferia/common/tests/unified_config/fixtures/valid.yaml" \
  python -c "from inferia.services.api_gateway.config import settings; print(settings.host, settings.port, settings.environment)"
```

Expected: `0.0.0.0 8000 development` (port 8000 came from yaml).

- [ ] **Step 7: Show diff and commit**

```bash
git add package/src/inferia/services/api_gateway/config.py
git diff --staged
```

Pause for user review. Note the diff is small — just the imports + class line + ClassVar.

```bash
git commit -S -m "feat(api-gateway): migrate Settings to UnifiedBaseSettings (#243)"
```

---

## Task 8: CLI `--config` flag + multiprocessing env plumbing

**Files:**
- Create: `package/src/inferia/common/tests/unified_config/test_cli_flag.py`
- Modify: `package/src/inferia/cli.py:617-694`

- [ ] **Step 1: Write the failing tests**

Create `package/src/inferia/common/tests/unified_config/test_cli_flag.py`:

```python
"""Tests that the CLI's --config flag plumbs INFERIA_CONFIG into the process env
before any service Settings is constructed."""
import os
import sys
from unittest.mock import patch
import pytest

from inferia import cli as cli_module


@pytest.fixture
def stub_runners(monkeypatch):
    """Stub out the actual service runners so main() doesn't try to start anything."""
    seen: dict[str, str | None] = {}

    def _record(*args, **kwargs):
        seen["INFERIA_CONFIG"] = os.environ.get("INFERIA_CONFIG")

    for name in (
        "run_all",
        "run_api_gateway_service",
        "run_inference_service",
        "run_guardrail_service",
        "run_data_service",
        "run_orchestration_stack",
        "run_skypilot_server",
        "run_init",
        "run_migrate",
    ):
        if hasattr(cli_module, name):
            monkeypatch.setattr(cli_module, name, _record)
    return seen


def test_config_flag_sets_env(stub_runners, monkeypatch):
    monkeypatch.delenv("INFERIA_CONFIG", raising=False)
    cli_module.main(["start", "api-gateway", "--config", "/tmp/inferia.yaml"])
    assert stub_runners["INFERIA_CONFIG"] == "/tmp/inferia.yaml"


def test_no_config_flag_leaves_env_alone(stub_runners, monkeypatch):
    monkeypatch.delenv("INFERIA_CONFIG", raising=False)
    cli_module.main(["start", "api-gateway"])
    assert stub_runners["INFERIA_CONFIG"] is None


def test_config_flag_with_metacharacters_passes_through(stub_runners, monkeypatch):
    """Path is taken as a literal — no shell eval."""
    monkeypatch.delenv("INFERIA_CONFIG", raising=False)
    cli_module.main(["start", "api-gateway", "--config", "/tmp/a;rm -rf b"])
    assert stub_runners["INFERIA_CONFIG"] == "/tmp/a;rm -rf b"


def test_config_flag_overrides_pre_existing_env(stub_runners, monkeypatch):
    monkeypatch.setenv("INFERIA_CONFIG", "/old/path.yaml")
    cli_module.main(["start", "api-gateway", "--config", "/new/path.yaml"])
    assert stub_runners["INFERIA_CONFIG"] == "/new/path.yaml"
```

- [ ] **Step 2: Run tests, expect fail**

```bash
pytest package/src/inferia/common/tests/unified_config/test_cli_flag.py -v 2>&1 | tail -20
```

Expected: argparse error `unrecognized arguments: --config` or similar.

- [ ] **Step 3: Add `--config` to the `start` sub-parser**

In `package/src/inferia/cli.py`, find the `start_parser` block at lines 617-638:

```python
    start_parser = sub.add_parser("start", help="Start Inferia services")
    start_parser.add_argument(
        "service",
        nargs="?",
        default="all",
        choices=[
            "all",
            "api-gateway",
            "inference",
            "orchestration",
            "guardrail",
            "data",
            "skypilot",
        ],
        help="Service to start (default: all)",
    )
    start_parser.add_argument(
        "--env",
        choices=["dev", "production"],
        default="production",
        help="Environment to run in (default: production)",
    )
```

Append a new argument right after the `--env` block (before `args, unknown = parser.parse_known_args(argv)`):

```python
    start_parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to unified YAML config file (default: auto-discover via "
             "INFERIA_CONFIG, ./inferia.yaml, or /etc/inferia/inferia.yaml)",
    )
```

- [ ] **Step 4: Plumb `--config` to INFERIA_CONFIG before dispatching**

In `package/src/inferia/cli.py`, find the `cmd == "start":` branch starting at line 652. **Immediately after** `service = ...` and `env = ...`, **before** any of the `if service == ...` branches, add:

```python
            config_path = getattr(args, "config", None)
            if config_path is not None:
                os.environ["INFERIA_CONFIG"] = config_path
```

This sets the env var in the parent before `multiprocessing.Process.start()` is called, so every child inherits it.

- [ ] **Step 5: Run tests, expect pass**

```bash
pytest package/src/inferia/common/tests/unified_config/test_cli_flag.py -v
```

Expected: all green.

- [ ] **Step 6: Re-run the whole unified_config test suite to confirm no regression**

```bash
pytest package/src/inferia/common/tests/unified_config/ -v
```

- [ ] **Step 7: Show diff and commit**

```bash
git add package/src/inferia/cli.py \
        package/src/inferia/common/tests/unified_config/test_cli_flag.py
git diff --staged
```

Pause for user review.

```bash
git commit -S -m "feat(cli): --config flag plumbed into INFERIA_CONFIG before spawn (#243)"
```

---

## Task 9: `inferia.yaml.example` + Docker compose mount

**Files:**
- Create: `inferia.yaml.example` (repo root)
- Modify: `docker/docker-compose.yml:33-83` (inferia-unified service: add env + bind-mount)

- [ ] **Step 1: Create the example yaml**

Create `inferia.yaml.example` at the repo root. Contents:

```yaml
# inferia.yaml — InferiaLLM unified configuration (Phase 1 — api_gateway)
#
# Discovery:
#   1. inferiallm start --config /path/to/inferia.yaml
#   2. INFERIA_CONFIG env var
#   3. ./inferia.yaml in CWD
#   4. /etc/inferia/inferia.yaml
#
# Secrets MUST come from env via ${VAR} interpolation. Plain secrets in this
# file are rejected by the schema validator.
#
# Precedence (highest → lowest): CLI args > env > .env > this file > defaults
version: 1

environment: development
log_level: INFO

# ─── Shared infrastructure ──────────────────────────────────────────────
infra:
  database:
    url: ${DATABASE_URL:-postgresql+asyncpg://inferia:inferia@localhost:5432/inferia}
    ssl: false
  redis:
    host: ${REDIS_HOST:-localhost}
    port: 6379
    db: "0"
    username: ${REDIS_USERNAME:-}
    password: ${REDIS_PASSWORD:-}
    ssl: false
  logstash:
    host: ${LOGSTASH_HOST:-}
    port: 5959

# ─── Shared security ────────────────────────────────────────────────────
security:
  jwt_secret_key: ${JWT_SECRET_KEY:-placeholder-secret-key-at-least-32-chars-long}
  jwt_algorithm: HS256
  internal_api_key: ${INTERNAL_API_KEY:-dev-internal-api-key-32-bytes-min-length}
  secret_encryption_key: ${SECRET_ENCRYPTION_KEY:-}
  log_encryption_key: ${LOG_ENCRYPTION_KEY:-}
  allowed_origins:
    - http://localhost:3000
    - http://localhost:5173
    - http://localhost:8001

# ─── Per-service ────────────────────────────────────────────────────────
services:
  api_gateway:
    enabled: true
    host: 0.0.0.0
    port: 8000
    workers: 1
    reload: false
    proxy_headers: true
    default_org_name: "Default Organization"
    auth:
      provider: local
    superadmin:
      email: ${SUPERADMIN_EMAIL:-}
      password: ${SUPERADMIN_PASSWORD:-}
    rate_limit:
      enabled: false
      requests_per_minute: 10000
      burst_size: 1000
      use_redis: false
    http_client:
      service_timeout_seconds: 10.0
      service_connect_timeout_seconds: 3.0
      service_max_connections: 500
      service_max_keepalive: 100
      proxy_timeout_seconds: 300.0
      proxy_max_connections: 500
      proxy_max_keepalive: 100
    ssl:
      verify: true
    service_urls:
      guardrail:     http://localhost:8002
      data:          http://localhost:8003
      orchestration: http://localhost:8080
      inference:     http://localhost:8001

  # Phase 2+ — full schemas land in follow-up PRs.
  inference:     { enabled: true }
  guardrail:     { enabled: true }
  data:          { enabled: true }
  orchestration: { enabled: true }

# ─── Providers ──────────────────────────────────────────────────────────
# Phase 1 accepts arbitrary keys here (Phase 2 will tighten).
providers:
  cloud:
    aws:
      access_key_id: ${AWS_ACCESS_KEY_ID:-}
      secret_access_key: ${AWS_SECRET_ACCESS_KEY:-}
      region: ap-south-1
  vectordb:
    chroma:
      api_key: ${CHROMA_API_KEY:-}
      tenant:  ${CHROMA_TENANT:-}
      url:     ${CHROMA_URL:-}
      is_local: true
  guardrails:
    groq:
      api_key: ${GROQ_API_KEY:-}
    lakera:
      api_key: ${LAKERA_API_KEY:-}
  depin:
    nosana:
      wallet_private_key: ${NOSANA_WALLET_KEY:-}
      api_keys:
        - name: prod
          key:  ${NOSANA_PROD_KEY:-}
          is_active: true
    akash:
      mnemonic: ${AKASH_MNEMONIC:-}
```

- [ ] **Step 2: Sanity-check the example loads**

```bash
INFERIA_CONFIG="$PWD/inferia.yaml.example" python -c "
from inferia.common.unified_config import load_unified_config, _clear_cache if False else load_unified_config
from inferia.common.unified_config.loader import _clear_cache
_clear_cache()
cfg = load_unified_config()
print('OK', cfg.version, cfg.services.api_gateway.port)
"
```

Expected: `OK 1 8000`

- [ ] **Step 3: Add env + bind-mount to docker-compose unified service**

In `docker/docker-compose.yml`, find the `inferia-unified` service block at lines 33-83. In the `environment:` list, add (alongside the existing entries):

```yaml
      - INFERIA_CONFIG=/etc/inferia/inferia.yaml
```

In the `volumes:` list (currently `- appdata:/data`), add a second entry:

```yaml
    volumes:
      - appdata:/data
      - ../inferia.yaml.example:/etc/inferia/inferia.yaml:ro
```

(We mount `inferia.yaml.example` — production deploys override the host path to their own `inferia.yaml`.)

- [ ] **Step 4: Show diff and commit**

```bash
git add inferia.yaml.example docker/docker-compose.yml
git diff --staged
```

Pause for user review.

```bash
git commit -S -m "feat(docker): mount inferia.yaml.example and wire INFERIA_CONFIG (#243)"
```

---

## Task 10: Final coverage gate + Mistakes Log update + Docker smoke

**Files:**
- Modify: `CLAUDE.md` (append to Mistakes Log section)

- [ ] **Step 1: Run the full unified_config suite with the coverage gate**

```bash
pytest package/src/inferia/common/tests/unified_config/ \
       --cov=inferia.common.unified_config \
       --cov-report=term-missing \
       --cov-fail-under=95
```

Expected: PASS, coverage ≥95%. If under, add tests for the missing lines and re-run before continuing.

- [ ] **Step 2: Run the full project test suite**

```bash
make test
```

Expected: green (no regressions in api_gateway, inference, guardrail, data, orchestration, or common tests).

- [ ] **Step 3: Update CLAUDE.md Mistakes Log**

In `CLAUDE.md`, find the `## Mistakes Log` section. Append:

```markdown
- **Unified config: Pydantic Settings v2 source order is significant.** Appending
  vs. inserting a custom source changes precedence silently. Always assert order
  in a test (see `package/src/inferia/common/tests/unified_config/test_base.py`).
- **Unified config: `yaml.safe_load("")` returns `None`, not `{}`.** Wrap with
  `or {}` in `load_yaml` or the loader will `AttributeError` on the empty-file
  path.
- **Unified config: `os.environ` reads at fork time.** Child multiprocessing
  workers see the parent's env at fork. Set `INFERIA_CONFIG` in the CLI *before*
  `multiprocessing.Process.start()`.
- **Unified config: `${VAR}` with an empty env var is not the same as unset.**
  `${VAR:-default}` treats empty as unset (falls back). `${VAR-default}` keeps
  the empty value. Mirror POSIX shell semantics; document the distinction at the
  call site.
```

- [ ] **Step 4: Docker smoke test**

```bash
# Build
make docker-build-unified
```

Expected: image built, no PyYAML installation errors.

```bash
# Bring up
make docker-up-unified
```

```bash
# Wait for health
until curl -sf http://localhost:8000/health; do echo waiting; sleep 2; done
```

Expected: healthy within ~30s.

```bash
# Confirm the yaml was discovered
docker logs inferia-unified 2>&1 | grep -i "unified_config\|inferia.yaml" | head -5
```

Expected output contains a log line like:
```
[unified_config] discovered yaml: /etc/inferia/inferia.yaml (source: INFERIA_CONFIG)
```

(We may need to add this log line if it's missing — if so, add a `logger.info(...)` at the top of `load_unified_config` in `loader.py` and rebuild.)

```bash
# Teardown
make docker-down
```

- [ ] **Step 5: Show diff and commit**

```bash
git add CLAUDE.md
git diff --staged
```

Pause for user review.

```bash
git commit -S -m "docs: add unified-config entries to Mistakes Log (#243)"
```

---

## Task 11: Push branch + open PR

- [ ] **Step 1: Verify all commits are SSH-signed**

```bash
git log --show-signature main..HEAD 2>&1 | head -50
```

Expected: every commit shows `Good "ssh-…" signature` and the author is
`Ankit Prasad <ankitprasad2005@outlook.com>`. No `Co-Authored-By: Claude`
trailers anywhere in `git log main..HEAD --format=%B`.

- [ ] **Step 2: Quick author/trailer audit**

```bash
git log main..HEAD --format='%h %ae %s' && echo "---" && git log main..HEAD --format=%B | grep -i claude && echo "FOUND CLAUDE — fix before pushing" || echo "clean"
```

Expected: `clean`.

- [ ] **Step 3: Push the branch**

```bash
git push -u origin feat/issue-243-unified-config
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "feat(#243): unified config file — Phase 1 (loader + api_gateway)" --body "$(cat <<'EOF'
## Summary
- Phase 1 of [#243](https://github.com/InferiaAI/InferiaLLM/issues/243): introduce `inferia.yaml` as a deterministic, swarm-friendly config source.
- New `inferia.common.unified_config` sub-package: loader, schema, source class, base mixin. Precedence: CLI > env > .env > yaml > defaults.
- `${VAR}` / `${VAR:-default}` / `${VAR-default}` interpolation; plain secrets rejected.
- `services.<name>.enabled` flag for per-node selectivity.
- `api_gateway` migrated; other services unchanged this PR (Phase 2+).
- CLI: `inferiallm start --config <path>` plumbed through multiprocessing as `INFERIA_CONFIG`.
- Docker compose mounts `inferia.yaml.example` into the unified container at `/etc/inferia/inferia.yaml`.
- Unit tests cover discovery, interpolation grammar, schema validation, source merging, and CLI plumbing; coverage ≥95% on the new sub-package.

Design spec: `docs/superpowers/specs/2026-05-12-unified-config-design.md`
Plan: `docs/superpowers/plans/2026-05-12-unified-config-plan.md`

## Test plan
- [x] `pytest package/src/inferia/common/tests/unified_config/ --cov-fail-under=95` ≥95%
- [x] `make test` green (no regressions in api_gateway / inference / guardrail / data / orchestration / common)
- [x] `make docker-up-unified` + `curl http://localhost:8000/health` returns 200
- [x] Docker logs show `[unified_config] discovered yaml: /etc/inferia/inferia.yaml`
- [x] Removing the bind-mount and restarting still boots (env+defaults fallback)
- [x] Invalid yaml (`version: 99`) aborts startup with `ConfigValidationError`
EOF
)"
```

- [ ] **Step 5: Confirm PR URL with the user**

Echo the URL gh just printed. Done.

---

## Self-review checklist (run before handoff)

- **Spec coverage**
  - [x] Sections 1–4 (problem / goals / non-goals / decisions): Tasks 0–10 honor every decision in the decisions table.
  - [x] Section 5 (architecture): Tasks 1, 4, 6 build the four modules.
  - [x] Section 6 (yaml schema): Task 4 schema models, Task 9 example yaml.
  - [x] Section 7 (loader & interpolation): Tasks 2, 3, 5.
  - [x] Section 8 (source class & precedence): Task 6.
  - [x] Section 9 (testing — ≥95% coverage): every TDD task has a coverage step; Task 10 is the final gate.
  - [x] Section 10 (docker validation): Task 10.
  - [x] Section 11 (rollout & commits): 8 SSH-signed commits matching the spec's chain.
  - [x] Section 12 (mistakes log additions): Task 10 step 3.
  - [x] Section 13 (acceptance criteria): Task 10 + Task 11.

- **Placeholder scan:** No "TBD", "TODO", "similar to Task N", "add appropriate error handling". Every code step shows the exact code.

- **Type / name consistency:** `interpolate_env`, `find_config_path`, `load_yaml`, `validate_schema`, `load_unified_config`, `_clear_cache`, `YamlConfigSettingsSource`, `UnifiedBaseSettings`, `InferiaConfig`, `KNOWN_PLACEHOLDER_SECRETS`, `_yaml_path` all match across tasks.

- **Acceptance criteria coverage:** Each item in spec §13 has a corresponding verification step (Task 6 step 8 = #4, Task 7 step 4 = #5, Task 10 step 4 = #6, Task 7 step 2 = #2/#3, Task 6 step 9 + Task 8 = #1).
