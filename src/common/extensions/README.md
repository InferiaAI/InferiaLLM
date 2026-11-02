# common.extensions

Extension points: a `Protocol` per kind with an open-source default, selected
by configuration. Contract: `docs/platform/interfaces.md` §6. This is the seam
the later open-core plugin system grows from, so it stays small and imports
only the standard library.

| Kind | Protocol | OSS default | Called from |
|---|---|---|---|
| `audit_sink` | `emit(event)` | `api_gateway.audit.sinks:PostgresAuditSink` | `AuditService.log_event` |
| `usage_sink` | `record(events, *, db)` | `api_gateway.policy.extensions:PolicyEngineUsageSink` | `POST /internal/policy/track_usage` (background) |
| `quota_policy` | `check(ctx, estimate, *, db)` | `api_gateway.policy.extensions:PolicyEngineQuotaPolicy` | `POST /internal/policy/check_quota` |
| `token_verifier` | `verify(token, *, db)` | `api_gateway.rbac.token_verifier:DefaultTokenVerifier` | HTTP auth middleware and WebSocket auth |
| `admission_policy` | reserved | none yet | registered by the inference service |

## Selecting an implementation

```bash
INFERIA_EXT_AUDIT_SINK=mycompany.audit:SiemAuditSink
```

The value is a packaging object reference (`module:Attr`). The class is
instantiated with no arguments. The gateway calls `extensions.validate(...)`
first thing in its lifespan, so a bad value (empty, malformed, not importable,
not a class, missing or non-async protocol methods, unknown `INFERIA_EXT_*`
name) stops startup with an `ExtensionConfigError` that names the variable.
There is no fallback to the default.

Notes for implementers: the HTTP auth middleware caches a `token_verifier`
result per token for 30 s (`rbac/middleware.py::_auth_cache`); audit and usage
sinks run off the request path (task / background task), and an exception from
an audit sink is logged, not raised.

## Adding a kind from another package

```python
from common import extensions
extensions.register_kind("admission_policy", AdmissionPolicy,
                         default="inference.core.admission:DefaultAdmissionPolicy")
```

Then call `extensions.validate([...kinds this service uses])` at startup.

## Tests

`extensions.override(kind, impl)` swaps in an instance for the duration of a
`with` block; `extensions.reset()` clears cached instances.
