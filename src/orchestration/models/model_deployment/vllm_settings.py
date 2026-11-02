"""vLLM runtime settings: one reading of a deployment's configuration for every path.

The dashboard and API store them at the top of ``configuration``. The nested
``config`` block is the older form the worker reads, so it wins where both set
a key. An unset, ``auto`` or off value is left out, so the engine or the
worker's GPU profile decides it.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

VLLM_ENGINES = frozenset({"vllm", "vllm-omni"})

# These become command-line values, so a name can't start with a dash. Inside
# one a dash is fine: "compressed-tensors" is a real quantization.
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _positive_int(value: Any) -> int:
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ValueError
    number = int(value)
    if number <= 0:
        raise ValueError
    return number


def _fraction(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError
    number = float(value)
    if not 0 < number <= 1:
        raise ValueError
    return number


def _name(value: Any) -> str:
    if not isinstance(value, str) or not _NAME.match(value):
        raise ValueError
    return value


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ValueError


# configuration key -> (key the engine takes, parser, what a valid value is)
_FIELDS = {
    "max_model_len": ("max_model_len", _positive_int, "a positive whole number"),
    "max_num_seqs": ("max_num_seqs", _positive_int, "a positive whole number"),
    "gpu_memory_utilization": ("gpu_memory_utilization", _fraction, "a number above 0, up to 1"),
    "gpu_util": ("gpu_memory_utilization", _fraction, "a number above 0, up to 1"),
    "dtype": ("dtype", _name, "a data type name"),
    "kv_cache_dtype": ("kv_cache_dtype", _name, "a data type name"),
    "quantization": ("quantization", _name, "a quantization name"),
    "enforce_eager": ("enforce_eager", _flag, "true or false"),
    "trust_remote_code": ("trust_remote_code", _flag, "true or false"),
}


def _as_dict(configuration: Any) -> dict:
    if isinstance(configuration, str):
        try:
            configuration = json.loads(configuration)
        except ValueError:
            return {}
    return configuration if isinstance(configuration, dict) else {}


def _parse(configuration: Any) -> tuple[dict, list[str]]:
    cfg = _as_dict(configuration)
    settings: dict = {}
    errors: list[str] = []
    for key, (target, parse, expected) in _FIELDS.items():
        raw = cfg.get(key)
        if raw is None or raw == "" or (raw == "auto" and parse is _name):
            continue
        try:
            value = parse(raw)
        except (TypeError, ValueError):
            errors.append(f"{key} must be {expected}")
            continue
        if value is False:
            continue
        settings.setdefault(target, value)
    return settings, errors


def invalid_settings(configuration: Any) -> list[str]:
    """What is wrong with a configuration's settings; empty when it can be used."""
    return _parse(configuration)[1]


def settings_from(configuration: Any) -> dict:
    """The settings to hand the engine. Rows from before validation may hold a
    bad value: it is skipped, because failing here would strand the deployment."""
    settings, errors = _parse(configuration)
    if errors:
        logger.warning("Ignoring invalid vLLM settings: %s", "; ".join(errors))
    nested = _as_dict(configuration).get("config")
    return {**settings, **nested} if isinstance(nested, dict) else settings


async def settings_for_dispatch(
    configuration: Any, artifact_uri: str, hf_token: Optional[str] = None,
) -> dict:
    """settings_from, with what vLLM needs of the model's own context: it refuses
    to start above it. max_model_len is held to it here, and native_max_model_len
    lets the worker hold its GPU-profile default to it too. Unknown changes nothing."""
    from orchestration.models.model_deployment.preflight import fetch_native_max_len

    settings = settings_from(configuration)
    scheme, _, model_id = str(artifact_uri).rpartition("://")
    if scheme not in ("", "hf"):  # only Hugging Face can be asked
        return settings
    native = await fetch_native_max_len(model_id, hf_token)
    if not native:
        return settings
    settings["native_max_model_len"] = native
    requested = settings.get("max_model_len")
    try:
        if requested and int(requested) > native:
            logger.info("vLLM max_model_len for %s: %s -> %s", model_id, requested, native)
            settings["max_model_len"] = native
    except (TypeError, ValueError):
        settings.pop("max_model_len")
    return settings
