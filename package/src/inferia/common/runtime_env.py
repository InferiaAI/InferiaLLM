"""CP-side detection of the runtime environment InferiaLLM itself runs in.

Used for telemetry and logging only — load-bearing detection lives on the
worker side (inferia-worker/internal/cloudenv).

Cached for the process lifetime; reset via ``_CACHE.clear()`` in tests.
"""
from __future__ import annotations

import os
from typing import Literal

import httpx

RuntimeEnv = Literal["local", "aws-ec2", "k8s", "unknown"]

_CACHE: dict[str, RuntimeEnv] = {}
_IMDS_TIMEOUT_S = 0.2


def detect_runtime_env() -> RuntimeEnv:
    """Return the detected runtime environment, caching the result per-process.

    Resolution order:
    1. ``INFERIA_RUNTIME_ENV`` env var (wins if set).
    2. IMDSv2 probe against ``INFERIA_CLOUDENV_IMDS_URL``
       (defaults to ``http://169.254.169.254``).
    3. ``"local"`` if the probe fails or returns unexpected data.
    """
    cached = _CACHE.get("env")
    if cached is not None:
        return cached
    env = _detect()
    _CACHE["env"] = env
    return env


def _detect() -> RuntimeEnv:
    """Perform the actual detection without consulting the cache."""
    v = os.getenv("INFERIA_RUNTIME_ENV")
    if v:
        return v[:64]  # type: ignore[return-value]

    base = os.getenv("INFERIA_CLOUDENV_IMDS_URL", "http://169.254.169.254")
    try:
        with httpx.Client(timeout=_IMDS_TIMEOUT_S) as client:
            tok = client.put(
                f"{base}/latest/api/token",
                headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
            )
            if tok.status_code != 200:
                return "local"
            doc = client.get(
                f"{base}/latest/dynamic/instance-identity/document",
                headers={"X-aws-ec2-metadata-token": tok.text},
            )
            if doc.status_code == 200 and "instanceId" in doc.json():
                return "aws-ec2"
    except Exception:  # noqa: BLE001
        return "local"
    return "local"
