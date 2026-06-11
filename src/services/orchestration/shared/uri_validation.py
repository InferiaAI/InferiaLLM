"""
Shared URI scheme + runtime-config validators.

Originally lived in services.orchestration.llmd.spec_builder.
Extracted here so the new worker_controller can reuse them after llmd is
removed.

Used to validate inputs that flow into model-deployment commands sent to a
worker (or, previously, into a k8s CRD).
"""

import re

# Allowed URI schemes for model artifacts.
_ALLOWED_URI_SCHEMES = frozenset({"s3", "gs", "hf", "http", "https", "oci"})

# URI must start with scheme:// and contain no shell metacharacters.
_URI_PATTERN = re.compile(r"^[a-z][a-z0-9+\-\.]*://[^\x00-\x1f`$;|&><]+$", re.ASCII)

# Allowed runtime-config keys (subset of vLLM / TGI knobs we will pass through).
_ALLOWED_CONFIG_KEYS = frozenset({
    "tensor_parallel_size",
    "pipeline_parallel_size",
    "dtype",
    "max_model_len",
    "max_num_seqs",
    "gpu_memory_utilization",
    "quantization",
    "enforce_eager",
    "trust_remote_code",
    "max_batch_size",
    "max_input_length",
    "max_total_tokens",
})


def validate_artifact_uri(uri: str) -> str:
    """Validate artifact_uri against allowed schemes and safe characters.

    Raises ValueError on any unsafe input. Returns the URI unchanged on success.
    """
    if not uri or not isinstance(uri, str):
        raise ValueError("artifact_uri must be a non-empty string")

    if not _URI_PATTERN.match(uri):
        raise ValueError(f"artifact_uri contains invalid characters: {uri!r}")

    scheme = uri.split("://", 1)[0].lower()
    if scheme not in _ALLOWED_URI_SCHEMES:
        raise ValueError(
            f"artifact_uri scheme {scheme!r} not in allowed list: "
            f"{sorted(_ALLOWED_URI_SCHEMES)}"
        )

    return uri


def sanitize_config(config: dict | None) -> dict:
    """Filter config to only allowed runtime keys with safe scalar values.

    Unknown keys are dropped silently. Values that aren't safe scalars (str,
    int, float, bool — but not nested dicts/lists) are dropped too.
    """
    if not config:
        return {}

    sanitized = {}
    for key, value in config.items():
        if key not in _ALLOWED_CONFIG_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)):
            sanitized[key] = value

    return sanitized


# Backwards-compatible private aliases for any code that still imports the
# leading-underscore names. Remove once all call sites are migrated.
_validate_artifact_uri = validate_artifact_uri
_sanitize_config = sanitize_config
