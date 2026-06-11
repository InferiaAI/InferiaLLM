"""Exception hierarchy for the unified config loader.

All errors raised by `common.unified_config` inherit from
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
