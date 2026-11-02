"""Extension registry: one implementation per extension *kind*.

Contract: docs/platform/interfaces.md §6; plan.md §3 rule 2.

Selection, per kind, at first use:

1. a test override installed with :func:`override`, else
2. ``INFERIA_EXT_<KIND>=package.module:Attr`` from the environment, else
3. the kind's open-source default (a dotted string, imported lazily).

The value uses the packaging "object reference" syntax
(``importable.module:object.attr``,
https://packaging.python.org/en/latest/specifications/entry-points/), so the
same strings work when entry points replace env vars after the open-core
split. The referenced object must be a class; it is instantiated with no
arguments and must satisfy the kind's Protocol.

A bad value is an error, never a silent fallback to the default: a plugin
that was configured but not loaded (say, an enterprise audit sink) must stop
the service, not quietly lose events. :func:`validate` is called at startup
so misconfiguration fails before the service takes traffic.

Defaults are referenced by string so this package imports only the standard
library; core never imports plugin code, and the registry does not even
import the OSS defaults until they are used.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional

from common.extensions.protocols import AuditSink, QuotaPolicy, TokenVerifier, UsageSink

ENV_PREFIX = "INFERIA_EXT_"

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_SPEC_RE = re.compile(rf"^(?P<module>{_IDENT}(?:\.{_IDENT})*):(?P<attr>{_IDENT}(?:\.{_IDENT})*)$")
_KIND_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ExtensionConfigError(RuntimeError):
    """An extension kind could not be resolved to a valid implementation."""


@dataclass(frozen=True)
class Kind:
    name: str
    protocol: Optional[type]  # None = reserved: name claimed, contract not landed yet
    default: Optional[str]  # dotted "module:Attr", or None = no default
    description: str = ""


def _builtin_kinds() -> Dict[str, Kind]:
    kinds = [
        Kind(
            "audit_sink",
            AuditSink,
            "api_gateway.audit.sinks:PostgresAuditSink",
            "Audit events; default writes the audit_logs table.",
        ),
        Kind(
            "usage_sink",
            UsageSink,
            "api_gateway.policy.extensions:PolicyEngineUsageSink",
            "Durable usage records; default upserts the daily usage_stats rows.",
        ),
        Kind(
            "quota_policy",
            QuotaPolicy,
            "api_gateway.policy.extensions:PolicyEngineQuotaPolicy",
            "Request admission against budgets; default is the policy engine's daily quota.",
        ),
        Kind(
            "token_verifier",
            TokenVerifier,
            "api_gateway.rbac.token_verifier:DefaultTokenVerifier",
            "Bearer token -> UserContext; default dispatches on AUTH_PROVIDER.",
        ),
        # Session 3 registers the protocol and default from its own package
        # with register_kind("admission_policy", AdmissionPolicy, default=...).
        Kind("admission_policy", None, None, "Reserved for Session 3 (interfaces.md §6)."),
    ]
    return {k.name: k for k in kinds}


_lock = threading.RLock()
_kinds: Dict[str, Kind] = _builtin_kinds()
_instances: Dict[str, Any] = {}
_overrides: Dict[str, Any] = {}


def env_var(kind: str) -> str:
    return ENV_PREFIX + kind.upper()


def kinds() -> Mapping[str, Kind]:
    with _lock:
        return dict(_kinds)


def register_kind(
    name: str, protocol: type, default: Optional[str] = None, description: str = ""
) -> None:
    """Declare a new kind, or fill in a reserved one (protocol not yet set)."""
    if not _KIND_RE.match(name):
        raise ValueError(f"extension kind must match {_KIND_RE.pattern}: {name!r}")
    if default is not None and not _SPEC_RE.match(default):
        raise ValueError(f"default for {name!r} must be 'module:Attr': {default!r}")
    with _lock:
        existing = _kinds.get(name)
        if existing is not None and existing.protocol is not None:
            raise ValueError(f"extension kind {name!r} is already registered")
        _kinds[name] = Kind(name, protocol, default, description or (existing.description if existing else ""))
        _instances.pop(name, None)


def get(kind: str) -> Any:
    """Return the (cached) implementation for ``kind``."""
    with _lock:
        if kind in _overrides:
            return _overrides[kind]
        if kind in _instances:
            return _instances[kind]
        spec = _kinds.get(kind)
        if spec is None:
            raise ExtensionConfigError(
                f"unknown extension kind {kind!r}; known: {', '.join(sorted(_kinds))}"
            )
        impl = _resolve(spec)
        _instances[kind] = impl
        return impl


def validate(kinds_in_use: Iterable[str]) -> Dict[str, Any]:
    """Fail fast at startup.

    - Any ``INFERIA_EXT_*`` variable that names no known kind is an error (a
      typo would otherwise be ignored and the default silently used).
    - Every kind in ``kinds_in_use`` is resolved (its default is imported).
    - Every kind with an env override is resolved, even if this process does
      not use it, so a bad value is caught wherever it is set.
    - Reserved kinds with neither default nor override are skipped.
    """
    with _lock:
        known = {env_var(k): k for k in _kinds}
        unknown = sorted(k for k in os.environ if k.startswith(ENV_PREFIX) and k not in known)
        if unknown:
            raise ExtensionConfigError(
                f"unknown extension variable(s) {', '.join(unknown)}; "
                f"known: {', '.join(sorted(known))}"
            )
        wanted = set(kinds_in_use) | {k for var, k in known.items() if var in os.environ}
        resolved: Dict[str, Any] = {}
        for kind in sorted(wanted):
            spec = _kinds.get(kind)
            if spec is not None and spec.default is None and env_var(kind) not in os.environ:
                continue
            resolved[kind] = get(kind)
        return resolved


@contextlib.contextmanager
def override(kind: str, impl: Any) -> Iterator[Any]:
    """Temporarily force ``impl`` for ``kind`` (tests)."""
    with _lock:
        spec = _kinds.get(kind)
        if spec is None:
            raise ExtensionConfigError(f"unknown extension kind {kind!r}")
        if spec.protocol is not None:
            _check_protocol(spec, impl, where=f"override for {kind!r}")
        previous = _overrides.get(kind, _MISSING)
        _overrides[kind] = impl
    try:
        yield impl
    finally:
        with _lock:
            if previous is _MISSING:
                _overrides.pop(kind, None)
            else:
                _overrides[kind] = previous


def reset() -> None:
    """Forget cached instances, overrides and non-builtin kinds (tests)."""
    global _kinds
    with _lock:
        _kinds = _builtin_kinds()
        _instances.clear()
        _overrides.clear()


# ----------------------------------------------------------------- internals

_MISSING = object()


def _resolve(spec: Kind) -> Any:
    var = env_var(spec.name)
    raw = os.environ.get(var)
    if raw is not None:
        value = raw.strip()
        where = f"{var}={raw!r}"
        if not value:
            raise ExtensionConfigError(
                f"{where}: value is empty; unset it to use the default "
                f"({spec.default or 'none'})"
            )
    elif spec.default is not None:
        value = spec.default
        where = f"default {value!r} for {spec.name!r} (override with {var})"
    else:
        raise ExtensionConfigError(
            f"no implementation for extension kind {spec.name!r}: it has no default; set {var}"
        )
    if spec.protocol is None:
        raise ExtensionConfigError(
            f"{where}: kind {spec.name!r} is reserved and its protocol is not registered yet"
        )
    return _load(spec, value, where)


def _load(spec: Kind, value: str, where: str) -> Any:
    m = _SPEC_RE.match(value)
    if not m:
        raise ExtensionConfigError(f"{where}: expected 'package.module:Attribute' (module:attribute)")
    module_name, attr_path = m.group("module"), m.group("attr")
    try:
        obj: Any = importlib.import_module(module_name)
    except Exception as e:  # ImportError, or any error raised while importing the plugin
        raise ExtensionConfigError(f"{where}: cannot import module {module_name!r}: {e}") from e
    for part in attr_path.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError as e:
            raise ExtensionConfigError(
                f"{where}: module {module_name!r} has no attribute {attr_path!r}"
            ) from e
    if not inspect.isclass(obj):
        raise ExtensionConfigError(f"{where}: {attr_path!r} is not a class")
    try:
        impl = obj()
    except Exception as e:
        raise ExtensionConfigError(f"{where}: failed to instantiate {attr_path!r}: {e}") from e
    _check_protocol(spec, impl, where)
    return impl


def _protocol_methods(protocol: type) -> Dict[str, Any]:
    return {
        name: fn
        for name, fn in vars(protocol).items()
        if inspect.isfunction(fn) and not name.startswith("_")
    }


def _check_protocol(spec: Kind, impl: Any, where: str) -> None:
    assert spec.protocol is not None
    if not isinstance(impl, spec.protocol):
        missing = [n for n in _protocol_methods(spec.protocol) if not callable(getattr(impl, n, None))]
        raise ExtensionConfigError(
            f"{where}: {type(impl).__name__} does not implement {spec.protocol.__name__} "
            f"(missing: {', '.join(missing) or '?'})"
        )
    for name, fn in _protocol_methods(spec.protocol).items():
        if inspect.iscoroutinefunction(fn) and not inspect.iscoroutinefunction(getattr(impl, name)):
            raise ExtensionConfigError(
                f"{where}: {type(impl).__name__}.{name} must be async (async def)"
            )
