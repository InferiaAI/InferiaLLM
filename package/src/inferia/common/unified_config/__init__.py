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
