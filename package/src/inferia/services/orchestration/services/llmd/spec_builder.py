import re

# Allowed URI schemes for model artifacts
_ALLOWED_URI_SCHEMES = frozenset({"s3", "gs", "hf", "http", "https", "oci"})

# URI must start with scheme:// and contain no shell metacharacters
_URI_PATTERN = re.compile(r"^[a-z][a-z0-9+\-\.]*://[^\x00-\x1f`$;|&><]+$", re.ASCII)

# Allowed keys in the runtime config dict — only known vLLM / TGI runtime knobs
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


def _validate_artifact_uri(uri: str) -> str:
    """Validate artifact_uri against allowed schemes and safe characters."""
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


def _sanitize_config(config: dict | None) -> dict:
    """Filter config to only allowed runtime keys with safe scalar values."""
    if not config:
        return {}

    sanitized = {}
    for key, value in config.items():
        if key not in _ALLOWED_CONFIG_KEYS:
            continue
        # Only allow safe scalar types — no nested dicts/lists that could
        # inject arbitrary CRD structure
        if isinstance(value, (str, int, float, bool)):
            sanitized[key] = value

    return sanitized


def build_llmd_spec(
    *,
    deployment_id: str,
    model,
    replicas: int,
    gpu_per_replica: int,
    node_names: list[str],
):
    assert len(node_names) == replicas

    uri = _validate_artifact_uri(model["artifact_uri"])
    safe_config = _sanitize_config(model.get("config"))

    return {
        "apiVersion": "llmd.ai/v1",
        "kind": "LLMDeployment",
        "metadata": {
            "name": f"llmd-{deployment_id}",
            "labels": {
                "deployment_id": deployment_id
            },
        },
        "spec": {
            "replicas": replicas,
            "model": {
                "uri": uri,
                "format": "hf",
            },
            "runtime": {
                "backend": model["backend"],
                **safe_config,
            },
            "placement": {
                "nodeSelector": {
                    "kubernetes.io/hostname": node_names[0]
                }
            },
            "resources": {
                "limits": {
                    "nvidia.com/gpu": gpu_per_replica
                }
            },
            "service": {
                "type": "ClusterIP",
                "port": 8000
            }
        }
    }
