# Unified Config File — Design Spec

**Issue:** [#243](https://github.com/InferiaAI/InferiaLLM/issues/243) — Add deterministic configuration file for all features
**Branch:** `feat/issue-243-unified-config`
**Status:** Draft (in active brainstorming — sections being added as approved)
**Date:** 2026-05-12

> This document is updated section-by-section as the brainstorming flow progresses. Each section is appended only after the user approves the previous one.

---

## 1. Problem statement

InferiaLLM has five microservices, each with its own `BaseSettings` class reading
~80 distinct environment variables. There is no single file that captures the
operational state of the platform. This blocks:

- **Swarm deployment** — every node needs identical config but env vars are not
  trivially diffable or versionable.
- **Reproducibility** — "spin to any state" requires a deterministic, declarative
  input.
- **Auditability** — config drift between dev/staging/prod is invisible.

## 2. Goals

1. One file (`inferia.yaml`) that, together with secrets in env vars, fully
   determines how every InferiaLLM service runs.
2. Backward compatible: existing env-only deployments keep working unchanged.
3. Per-node selectivity (`services.<name>.enabled`) so the same yaml can drive
   different roles in a swarm.
4. Strong typing and schema validation — startup fails fast on invalid input.
5. Test coverage ≥95% on the new loader / source / base modules.

## 3. Non-goals

- Hot reload on file change (startup-only; restart applies new state).
- Replacing the dashboard's DB-driven provider edit UX (Phase 2 will replace it;
  this phase only loads providers from yaml at startup — but yaml is authoritative).
- Per-feature flag rollouts (existing feature flags stay in their current places).

## 4. Decisions (from brainstorming Q&A)

| Topic              | Decision                                                        |
|--------------------|-----------------------------------------------------------------|
| Format             | YAML                                                             |
| Secrets            | `${VAR}` / `${VAR:-default}` interpolation only. Plain secrets rejected. |
| Precedence         | CLI > env > yaml > pydantic defaults                             |
| Discovery          | `--config <path>` → `$INFERIA_CONFIG` → `./inferia.yaml` → `/etc/inferia/inferia.yaml` → none |
| Hot reload         | No — restart to apply                                            |
| Service toggle     | `services.<name>.enabled: bool` selects which services run       |
| Provider creds     | YAML is authoritative; overwrites DB on every start              |
| Rollout            | Phase 1: loader + schema + `api_gateway` only. Other services in follow-up PRs. |
| Implementation     | Approach A: Pydantic Settings v2 `customise_sources`             |

## 5. Architecture

```
                ┌──────────────────────────────────┐
                │     inferia.yaml (user-owned)    │
                │  schema-versioned, ${VAR}-interp │
                └────────────────┬─────────────────┘
                                 │
                  ┌──────────────▼──────────────┐
                  │   common/unified_config/    │
                  │  ┌───────────────────────┐  │
                  │  │ loader.py             │  │
                  │  │  - find_config_path() │  │
                  │  │  - load_yaml()        │  │
                  │  │  - interpolate_env()  │  │
                  │  │  - validate_schema()  │  │
                  │  └───────────────────────┘  │
                  │  ┌───────────────────────┐  │
                  │  │ source.py             │  │
                  │  │  YamlConfigSource     │  │  ← pydantic-settings
                  │  │   (PydanticBase-      │  │     PydanticBaseSettings-
                  │  │    SettingsSource)    │  │     Source subclass
                  │  └───────────────────────┘  │
                  │  ┌───────────────────────┐  │
                  │  │ schema.py             │  │
                  │  │  InferiaConfig (root) │  │
                  │  │   ├ services.*        │  │
                  │  │   ├ providers.*       │  │
                  │  │   └ infra.*           │  │
                  │  └───────────────────────┘  │
                  │  ┌───────────────────────┐  │
                  │  │ base.py               │  │
                  │  │  UnifiedBaseSettings  │  │ ← every service Settings
                  │  │   .settings_customise │  │   subclasses this
                  │  │    _sources()         │  │
                  │  └───────────────────────┘  │
                  └──────────────┬──────────────┘
                                 │
   ┌─────────────────────────────┼─────────────────────────────┐
   ▼                             ▼                             ▼
 api_gateway/                  inference/                  data/guardrail/
  config.py                    config.py                  orchestration/
  Settings(UnifiedBase…)       Settings(UnifiedBase…)     (Phase 2+)
```

### Module boundaries

- **`common/unified_config/loader.py`** — pure functions; reads disk, interpolates
  `${VAR}` and `${VAR:-default}`, returns a validated dict. No Pydantic-Settings
  coupling. Independently unit-testable.
- **`common/unified_config/source.py`** — Pydantic v2 `PydanticBaseSettingsSource`
  that delegates to the loader on first call. Lives strictly between env and
  defaults in the precedence chain.
- **`common/unified_config/schema.py`** — root `InferiaConfig` Pydantic model.
  The schema is the contract; loader/source consume it.
- **`common/unified_config/base.py`** — `UnifiedBaseSettings` mixin. Sets
  `settings_customise_sources` once; per-service `Settings` classes inherit it
  and declare which sub-tree of the YAML to read via a class-level
  `_yaml_path: ClassVar[str]` (e.g. `"services.api_gateway"`).
- **Per-service `config.py`** — only change: inherit from `UnifiedBaseSettings`
  and set `_yaml_path`. All existing fields, imports, and call sites stay the same.

### Phase 1 scope (this PR)

- All four modules above
- `api_gateway` migrated to `UnifiedBaseSettings`
- CLI gains `--config <path>` flag, plumbed through multiprocessing as
  `INFERIA_CONFIG` env var
- Top-level `inferia.yaml.example`
- Docker compose mounts `inferia.yaml` into the unified container at
  `/etc/inferia/inferia.yaml` and sets `INFERIA_CONFIG`
- Unit tests ≥95% coverage on loader + source + base
- Docker smoke test: build unified image, run with example yaml, hit `/health`

Out of scope (Phase 2+): inference, guardrail, data, orchestration migrations;
dashboard runtime config; provider seeding into DB.

## 6. YAML schema shape

The yaml mirrors the conceptual layout (services / providers / infra), not the
Python class layout. Phase 1 wires only the `services.api_gateway` and `infra.*`
subtrees; other branches are defined in the schema for forward-compat but
unused until Phase 2.

```yaml
# inferia.yaml — schema version 1
version: 1                          # required; loader fails on unknown major

environment: production             # development | staging | production
log_level: INFO                     # DEBUG | INFO | WARNING | ERROR | CRITICAL

# ─── Infrastructure (shared by all services) ──────────────────────────────
infra:
  database:
    url: ${DATABASE_URL:-postgresql+asyncpg://inferia:inferia@localhost:5432/inferia}
    ssl: true
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

# ─── Security (every service that needs these reads from here) ────────────
security:
  jwt_secret_key: ${JWT_SECRET_KEY}          # required, ≥32 chars
  jwt_algorithm: HS256
  internal_api_key: ${INTERNAL_API_KEY}      # required, ≥32 chars
  secret_encryption_key: ${SECRET_ENCRYPTION_KEY}
  log_encryption_key: ${LOG_ENCRYPTION_KEY:-}
  allowed_origins:
    - http://localhost:3000
    - http://localhost:5173

# ─── Per-service settings ────────────────────────────────────────────────
services:
  api_gateway:
    enabled: true
    host: 0.0.0.0
    port: 8000
    workers: 1
    reload: false
    proxy_headers: true
    forwarded_allow_ips: null
    default_org_name: "Default Organization"
    auth:
      provider: local                        # local | external
      external_url: null
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
      ca_bundle: null
    service_urls:
      guardrail:     http://localhost:8002
      data:          http://localhost:8003
      orchestration: http://localhost:8080
      inference:     http://localhost:8001

  inference:        { enabled: true }        # detailed fields land in Phase 2
  guardrail:        { enabled: true }
  data:             { enabled: true }
  orchestration:    { enabled: true }

# ─── Providers (yaml is authoritative; DB rows replaced on every start) ───
providers:
  cloud:
    aws:
      access_key_id: ${AWS_ACCESS_KEY_ID:-}
      secret_access_key: ${AWS_SECRET_ACCESS_KEY:-}
      region: ap-south-1
    gcp:
      project_id: ${GCP_PROJECT_ID:-}
      region: us-central1
      service_account_json: ${GCP_SERVICE_ACCOUNT_JSON:-}
  vectordb:
    chroma:
      api_key: ${CHROMA_API_KEY:-}
      tenant:  ${CHROMA_TENANT:-}
      url:     ${CHROMA_URL:-}
      is_local: true
      database: null
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

### 6.1 How a service Settings reads its sub-tree

```python
# package/src/inferia/services/api_gateway/config.py
from inferia.common.unified_config import UnifiedBaseSettings

class Settings(UnifiedBaseSettings):
    _yaml_path = "services.api_gateway"      # ← only declarative addition

    # existing fields unchanged — but values now flow from yaml when present
    host: str = "0.0.0.0"
    port: int = 8000
    jwt_secret_key: str = Field(..., validation_alias="JWT_SECRET_KEY")
    # …
```

Cross-cutting sections (`security.*`, `infra.*`) are auto-merged into each
service's view, so `Settings.jwt_secret_key` resolves from `security.jwt_secret_key`
in the yaml without each service having to know about it.

**Yaml-to-field-name rules:**
- Inside `services.<name>.*` — yaml keys are **flat Pydantic field names**.
  e.g. `services.api_gateway.service_http_timeout_seconds: 10.0`. The yaml may
  group readability-oriented sub-objects (e.g. `http_client:`, `ssl:`, `auth:`)
  — when present, the loader flattens them with the rule
  **`<group>.<leaf>` → field named `<group>_<leaf>` if it exists, else `<leaf>`**.
  The mapping is computed once at load time against each Settings class's
  declared field names — unknown leaves are rejected (typo protection).
- Inside `infra.*` and `security.*` — the leaf name becomes the field name
  directly (`infra.redis.host` → `redis_host`, `security.jwt_secret_key` →
  `jwt_secret_key`). Group prefixes (`redis_`, `database_`, `logstash_`) are
  prepended only when needed to disambiguate; the mapping is part of the
  schema and asserted by tests.

**Collision rule (enforced in `validate_schema`, not in the source):** if
`services.api_gateway.jwt_secret_key` *and* `security.jwt_secret_key` both
resolve to the same target field name, the loader raises at startup. Because
collisions are pre-validated, the source class's internal merge order is
irrelevant for correctness — but for clarity it merges shared sub-trees
first, then the service-specific sub-tree on top (so a future relaxation of
the collision rule would default to "service-specific wins").

### 6.2 Validation rules enforced at load time

- `version: 1` required; missing or unknown major → fatal
- Any value that still contains a literal `${VAR}` after interpolation → fatal
- `jwt_secret_key` and `internal_api_key`: ≥32 chars, no placeholder strings
- Unknown top-level keys → warning (forward compat); unknown keys inside a
  known sub-tree → fatal (typo protection)
- `services.<name>.enabled` must be bool

## 7. Loader & interpolation semantics

Everything in this section lives in `common/unified_config/loader.py` and is
pure-function — fully unit-testable without spinning a service.

### 7.1 Discovery — `find_config_path()`

```
1.  argv contains --config <path>          →  use that, fail if missing
2.  os.environ["INFERIA_CONFIG"]           →  use that, fail if missing
3.  ./inferia.yaml (relative to CWD)       →  use if exists
4.  /etc/inferia/inferia.yaml              →  use if exists
5.  return None                            →  loader is a no-op, env+defaults
```

Cases 1 and 2 are *explicit*: a path was requested. If the file doesn't exist
the loader raises `ConfigNotFoundError` (startup aborts). Cases 3 and 4 are
*implicit*: opportunistic. Case 5 keeps every existing env-only deployment
running unchanged.

The decision and resolved path are logged at INFO:
```
[unified_config] discovered yaml: /etc/inferia/inferia.yaml (source: INFERIA_CONFIG)
[unified_config] no yaml found; using env + defaults only
```

### 7.2 Load — `load_yaml(path) → dict`

- Uses `yaml.safe_load` (PyYAML). Never `yaml.load`. No custom tags.
- Empty file → `{}` (not None).
- YAML parse errors are re-raised as `ConfigParseError` with file + line.

### 7.3 Interpolate — `interpolate_env(obj) → obj`

Walks the parsed structure recursively. Only **string scalars** are
substituted; ints / bools / lists / dicts pass through (their *contents* are
walked).

Grammar:

```
${NAME}              →  os.environ["NAME"], else error
${NAME:-default}     →  env value if set and non-empty, else "default"
${NAME-default}      →  env value if set (even if empty), else "default"
$${literal}          →  $${literal} → ${literal}    (escape: literal "$" + "{")
```

Rules:
- Multiple substitutions in one string allowed:
  `host: "${HOST:-localhost}:${PORT:-6379}"` → `"localhost:6379"`.
- Substitution happens on the **string** level. Pydantic does type coercion
  at validate time.
- An unresolved `${VAR}` after interpolation is fatal — *not* silently empty.
- Trailing whitespace inside `${ }` is rejected. Names must match
  `[A-Z_][A-Z0-9_]*`.

### 7.4 Validate — `validate_schema(dict) → InferiaConfig`

Hands the interpolated dict to the root `InferiaConfig` Pydantic model.
`model_config = ConfigDict(extra="forbid")` catches typos inside known
sub-trees. Top-level unknown keys log a warning instead of erroring
(forward-compat).

Cross-validators:
- `version == 1` (current schema major)
- `internal_api_key` and `jwt_secret_key` under `security` — when present,
  ≥32 chars and not equal to known placeholder strings
- Each `services.<name>.enabled` is bool

### 7.5 Public API

```python
# inferia/common/unified_config/__init__.py
from .loader import load_unified_config, ConfigNotFoundError, ConfigParseError
from .schema import InferiaConfig
from .source import YamlConfigSettingsSource
from .base import UnifiedBaseSettings

__all__ = [
    "load_unified_config",
    "InferiaConfig",
    "YamlConfigSettingsSource",
    "UnifiedBaseSettings",
    "UnifiedConfigError",        # parent class
    "ConfigNotFoundError",
    "ConfigParseError",
    "ConfigInterpolationError",
    "ConfigValidationError",
]


def load_unified_config(path: str | None = None) -> InferiaConfig | None:
    """Find, load, interpolate, validate. None if no yaml was discovered.

    Cached per-process: subsequent calls return the same object.
    """
```

Cache is per-process. Each multiprocessing worker re-reads on first access:
env vars at fork time can differ between parent and child, and each child
should see its own resolution.

### 7.6 Error taxonomy

| Class                       | Cause                                       | Effect        |
|-----------------------------|---------------------------------------------|---------------|
| `ConfigNotFoundError`       | Explicit path (1, 2) missing                | Startup abort |
| `ConfigParseError`          | Invalid YAML syntax                         | Startup abort |
| `ConfigInterpolationError`  | `${VAR}` unresolved or malformed name       | Startup abort |
| `ConfigValidationError`     | Pydantic ValidationError on root schema     | Startup abort |

All four inherit from a single `UnifiedConfigError`. Each prints a tight,
actionable message (no full Pydantic traceback — just
`path: field 'services.api_gateway.port' → input 'abc' is not a valid integer`).

### 7.7 What does *not* live in the loader

- No Pydantic-Settings coupling. Loader returns a validated `InferiaConfig`
  object; the `YamlConfigSettingsSource` (Section 8) bridges it to per-service
  `Settings` classes.
- No file watching, no SIGHUP handler — out of scope (Section 4).

## 8. Source class & precedence wiring

### 8.1 The Pydantic-Settings precedence chain

Pydantic Settings v2 lets us declare an ordered list of sources via
`settings_customise_sources`. The first source to return a value for a field
wins. Our chain, highest → lowest precedence:

```
init_settings              ← Settings(field=value), incl. CLI-injected values
env_settings               ← os.environ + matching validation_alias
dotenv_settings            ← .env file
YamlConfigSettingsSource   ← NEW — the unified yaml
file_secret_settings       ← /run/secrets/* (Docker/K8s secret mounts)
field defaults             ← Pydantic field default
```

This matches the brainstorming decision: **CLI > env > yaml > defaults**, with
`.env` slotted between env and yaml (historical position) and Docker secret
files below yaml (rarely used, keep working).

### 8.2 `YamlConfigSettingsSource`

```python
# common/unified_config/source.py
from typing import Any, Tuple
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from .loader import load_unified_config


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """Reads field values from the unified yaml's per-service sub-tree."""

    def __init__(self, settings_cls: type[BaseSettings]):
        super().__init__(settings_cls)
        self._yaml_path: str | None = getattr(settings_cls, "_yaml_path", None)
        self._values: dict[str, Any] = self._build_values()

    def _build_values(self) -> dict[str, Any]:
        cfg = load_unified_config()           # cached
        if cfg is None or self._yaml_path is None:
            return {}

        node: Any = cfg
        for part in self._yaml_path.split("."):
            node = getattr(node, part, None)
            if node is None:
                return {}

        # Shared sub-trees go in first; service-specific overlays on top.
        # Collisions are pre-validated in validate_schema (Section 6.1),
        # so overlay order is for forward-compat only.
        merged: dict[str, Any] = {}
        merged.update(self._flatten(cfg.infra,     settings_cls=self.settings_cls))
        merged.update(self._flatten(cfg.security,  settings_cls=self.settings_cls))
        merged.update(self._flatten(node,          settings_cls=self.settings_cls))
        merged["environment"] = cfg.environment
        merged["log_level"] = cfg.log_level
        return merged

    def _flatten(
        self,
        node: Any,
        settings_cls: type[BaseSettings],
    ) -> dict[str, Any]:
        """Flatten one level of nested groups to Pydantic field names.

        For every `<group>.<leaf>` in `node`, emit:
          - `<group>_<leaf>` if that name is a declared field on settings_cls
          - else `<leaf>` if that name is a declared field
          - else skip (typo / forward-compat field)
        """
        ...

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

Properties:
- Honors `_yaml_path` so one source class serves every service.
- Auto-merges shared sub-trees (`security`, `infra`) into the per-service view.
- Returns nothing when no yaml was discovered — transparent in env-only deployments.
- No mutation of `os.environ` — env precedence stays env precedence.

### 8.3 `UnifiedBaseSettings`

```python
# common/unified_config/base.py
from typing import ClassVar, Tuple, Type
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from .source import YamlConfigSettingsSource


class UnifiedBaseSettings(BaseSettings):
    """Drop-in BaseSettings replacement that adds the yaml source."""

    _yaml_path: ClassVar[str | None] = None

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
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
```

### 8.4 Per-service migration — minimal diff

```diff
-from pydantic_settings import BaseSettings, SettingsConfigDict
+from inferia.common.unified_config import UnifiedBaseSettings

-class Settings(BaseSettings):
+class Settings(UnifiedBaseSettings):
+    _yaml_path = "services.api_gateway"
     # all existing fields unchanged
-    model_config = SettingsConfigDict(env_file=".env", ...)
```

Every existing `from ...config import settings` site continues to work — no
churn outside `config.py` itself.

### 8.5 CLI wiring

```python
# package/src/inferia/cli.py — new flag in main()
start_parser.add_argument(
    "--config",
    type=str,
    default=None,
    help="Path to unified YAML config file (default: auto-discover)",
)
```

When `--config` is passed, `main()` sets `os.environ["INFERIA_CONFIG"]`
*before* multiprocessing spawn so every child sees it. (Linux `fork`
inherits env; macOS `spawn` also propagates env.)

### 8.6 Docker plumbing

```yaml
# docker/docker-compose.yml — additions to inferia-unified service
inferia-unified:
  …
  environment:
    - INFERIA_CONFIG=/etc/inferia/inferia.yaml
  volumes:
    - appdata:/data
    - ../inferia.yaml:/etc/inferia/inferia.yaml:ro   # NEW
```

Bind-mount is optional: if `inferia.yaml` doesn't exist at the host path,
compose still starts (loader falls back to env). Production swarm deploys
pin a real path.

## 9. Testing

### 9.1 Layout

New tests under `package/src/inferia/common/tests/unified_config/`:

```
test_loader.py            ~30 cases
test_interpolation.py     ~25 cases
test_schema.py            ~20 cases
test_source.py            ~15 cases
test_base.py              ~10 cases
test_cli_flag.py          ~5  cases
fixtures/
  ├── valid.yaml
  ├── minimal.yaml
  ├── missing_required.yaml
  ├── bad_syntax.yaml
  ├── unknown_field.yaml
  ├── unresolved_var.yaml
  ├── secret_too_short.yaml
  └── collision.yaml
```

Per global CLAUDE.md: **≥95% coverage, all edge cases, all error paths**.

**Loader / discovery (`test_loader.py`)**
- `--config` flag → uses that path; raises `ConfigNotFoundError` if missing
- `INFERIA_CONFIG` env → same, plus `--config` overrides env when both set
- `./inferia.yaml` present → discovered
- `/etc/inferia/inferia.yaml` present (mocked) → discovered when CWD has none
- None of the above → returns `None`, no exception
- Empty file → defaults applied (not a crash)
- Cache: two calls return the same object identity
- Cache invalidates on different `path` argument

**Interpolation (`test_interpolation.py`)**
- `${VAR}` with VAR set → substituted
- `${VAR}` with VAR unset → `ConfigInterpolationError`
- `${VAR}` with VAR set to empty string → `ConfigInterpolationError`
- `${VAR:-fallback}` with VAR unset / empty → "fallback"
- `${VAR-fallback}` with VAR empty → "" (kept)
- `${VAR-fallback}` with VAR unset → "fallback"
- `${A}${B}` multi-substitution → concatenated
- `"port=${PORT:-8000}"` partial substitution → "port=8000"
- `$${literal}` escape → "${literal}"
- `${invalid-name}` (lowercase / dashes) → error
- `${ VAR }` whitespace inside braces → error
- Substitution inside lists and nested dicts walks correctly
- Non-string scalars (int, bool, None) pass through untouched
- **Length-overflow**: 10 MB string with thousands of `${VAR}` → terminates <1s
- **Recursion safety**: nested dict depth 100 doesn't blow the stack

**Schema (`test_schema.py`)**
- Missing `version` → fatal
- Unknown major (`version: 2`) → fatal
- `services.api_gateway.enabled: "true"` (string, not bool) → fatal
- `services.api_gateway.port: -1` → fatal
- `security.jwt_secret_key: "short"` (<32 chars) → fatal
- `jwt_secret_key` equal to known placeholder → fatal
- Unknown top-level key → warning, not fatal
- Unknown key inside known sub-tree → fatal
- Field-name collision (`services.X.k` + `security.k`) → fatal

**Source (`test_source.py`)**
- Field in yaml only → loaded
- Field in env only → env wins
- Field in both → env wins
- Field nowhere → pydantic default
- No yaml found → source returns `{}`
- `_yaml_path` reads correct sub-tree
- `_yaml_path = None` (subclass forgot) → empty, no crash
- Shared sub-tree merge: yaml has `security.jwt_secret_key`, Settings reads it
- **Length-overflow on a string field**: 10 MB string respects field constraints

**Base (`test_base.py`)**
- Subclass with no `_yaml_path` behaves like `BaseSettings`
- Precedence order matches Section 8.1 exactly
- `extra="ignore"` matches existing services

**CLI (`test_cli_flag.py`)**
- `inferiallm start --config /path` sets `INFERIA_CONFIG` before spawn
- `inferiallm start` (no flag) leaves env alone
- Path with shell metacharacters passes through as literal, not eval'd

### 9.2 Coverage gate

```
pytest --cov=inferia.common.unified_config --cov-fail-under=95
```

Runs before commit. `make test` continues to work unchanged; the gate is a
new pre-commit assertion specific to this sub-package.

## 10. Docker validation (manual smoke before merge)

```bash
# 1. Build unified image
make docker-build-unified

# 2. Drop example yaml at repo root
cp inferia.yaml.example inferia.yaml

# 3. Bring up the stack (compose mounts the yaml automatically)
make docker-up-unified

# 4. Wait for health
until curl -sf http://localhost:8000/health; do sleep 2; done

# 5. Confirm yaml was read
docker logs inferia-unified | grep "unified_config"
#   expected: [unified_config] discovered yaml: /etc/inferia/inferia.yaml

# 6. Negative: remove yaml, restart, env-only fallback still boots
mv inferia.yaml inferia.yaml.bak
docker restart inferia-unified
docker logs inferia-unified | grep "no yaml found"

# 7. Negative: bad yaml, confirm clean startup abort
echo "version: 99" > inferia.yaml
docker restart inferia-unified
docker logs inferia-unified | grep "ConfigValidationError"

# 8. Teardown
make docker-down
```

This is the user-facing gate: each step inspected *before* any signed commit.

## 11. Rollout & commits

Phase 1 PR is a small chain of commits, each individually reviewable.
**The user reviews each diff before it is signed and committed**:

1. `docs: spec for unified config (#243)`
2. `feat(common): unified-config loader, schema, source, base`
3. `test(common): unified-config tests (≥95% coverage)`
4. `feat(api-gateway): migrate Settings to UnifiedBaseSettings`
5. `feat(cli): --config flag + multiprocessing env plumbing`
6. `chore(docker): mount inferia.yaml.example into unified container`
7. `chore: add inferia.yaml.example at repo root`

No commit body credits Claude — per global CLAUDE.md.

**Phase 2+ (separate PRs, not this work):** inference, guardrail, data,
orchestration migrations; provider-seeding into DB; dashboard runtime config.

## 12. Mistakes log additions (CLAUDE.md)

To satisfy the project rule "never repeat the same mistake," the project
CLAUDE.md Mistakes Log gains:

- **Pydantic Settings v2 source order is significant** — appending vs.
  inserting changes precedence silently. Assert order in a test.
- **`yaml.safe_load("")` returns `None`, not `{}`** — wrap with `or {}` or the
  loader will `AttributeError` on the empty-file path.
- **`os.environ` reads at fork time** — children see the parent's env at fork.
  Set `INFERIA_CONFIG` in the CLI *before* `multiprocessing.Process.start()`.
- **`${VAR}` with empty env var is not the same as unset** — `${VAR:-default}`
  treats empty as unset; `${VAR-default}` keeps the empty. Document the
  distinction; mirror POSIX shell semantics.

## 13. Acceptance criteria

This PR is complete when, on `feat/issue-243-unified-config`:

1. All Phase 1 modules exist and import cleanly
2. `api_gateway` starts and runs with yaml-driven config in Docker
3. `api_gateway` still starts in env-only mode with no yaml present
4. `pytest --cov=inferia.common.unified_config` ≥95%
5. `make test` (full suite) is still green
6. Docker smoke sequence (Section 10) completes without errors

