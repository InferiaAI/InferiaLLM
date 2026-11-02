"""Extension points: Protocols with open-source defaults, chosen by config.

    from common import extensions
    sink = extensions.get("audit_sink")      # AuditSink
    await sink.emit(AuditEvent(action="..."))

Select an implementation with ``INFERIA_EXT_<KIND>=package.module:Class``.
See ``registry.py`` for the resolution rules and docs/platform/interfaces.md §6
for the contract. This package is the seam the later open-core plugin system
grows from (brief.md Part B), so keep it small and standard-library only.
"""

from common.extensions.registry import (
    ENV_PREFIX,
    ExtensionConfigError,
    Kind,
    env_var,
    get,
    kinds,
    override,
    register_kind,
    reset,
    validate,
)

__all__ = [
    "ENV_PREFIX",
    "ExtensionConfigError",
    "Kind",
    "env_var",
    "get",
    "kinds",
    "override",
    "register_kind",
    "reset",
    "validate",
]
