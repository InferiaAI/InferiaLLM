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
