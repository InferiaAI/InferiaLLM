"""DLAMI lookup helper.

Resolves the latest AWS Deep Learning AMI for Ubuntu 22.04 + NVIDIA driver
via SSM Public Parameters, with a per-region in-memory cache (TTL 1 h).

Sync (not async) — used inside Pulumi inline programs that themselves
run synchronously.
"""
from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

import boto3
import botocore.exceptions


class AMILookupError(RuntimeError):
    """Raised when the DLAMI SSM parameter is unreachable or missing."""


_DLAMI_PARAMETER = (
    "/aws/service/deeplearning/ami/x86_64/"
    "oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
)
# Generic Ubuntu 22.04 AMI for CPU-only instances (no NVIDIA driver).
_UBUNTU_PARAMETER = (
    "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
)
_DLAMI_TTL_S = 3600
_DLAMI_CACHE: Dict[str, Tuple[str, float]] = {}


def latest_dlami_ami(
    region: str,
    *,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    parameter_name: Optional[str] = None,
) -> str:
    """Return the latest DLAMI for ``region``.

    By default returns the DLAMI Ubuntu 22.04 + NVIDIA driver. Pass
    ``parameter_name=_UBUNTU_PARAMETER`` for the plain Ubuntu AMI used
    on CPU-only instances.

    Credentials are passed in explicitly so this works inside an
    orchestration container that has no AWS_* env vars at the process
    level — the Pulumi adapter resolves them from ProvidersConfig and
    threads them in. Falls back to the boto3 default chain when None.
    """
    name = parameter_name or _DLAMI_PARAMETER
    cache_key = f"{region}::{name}"
    now = time.time()
    cached = _DLAMI_CACHE.get(cache_key)
    if cached and (now - cached[1]) < _DLAMI_TTL_S:
        return cached[0]
    client_kwargs = {"region_name": region}
    if aws_access_key_id and aws_secret_access_key:
        client_kwargs["aws_access_key_id"] = aws_access_key_id
        client_kwargs["aws_secret_access_key"] = aws_secret_access_key
    ssm = boto3.client("ssm", **client_kwargs)
    try:
        resp = ssm.get_parameter(Name=name)
    except botocore.exceptions.ClientError as e:
        raise AMILookupError(f"DLAMI lookup failed: {e.response['Error']['Code']}") from e
    except botocore.exceptions.BotoCoreError as e:
        raise AMILookupError(f"DLAMI lookup failed: {type(e).__name__}") from e
    value = resp["Parameter"]["Value"]
    _DLAMI_CACHE[cache_key] = (value, now)
    return value


# Re-exported so callers can switch to the plain Ubuntu image for CPU-only
# instance types (NVIDIA driver isn't useful on a t3.micro).
PLAIN_UBUNTU_PARAMETER = _UBUNTU_PARAMETER
