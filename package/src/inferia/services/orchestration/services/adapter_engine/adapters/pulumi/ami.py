"""DLAMI lookup helper.

Resolves the latest AWS Deep Learning AMI for Ubuntu 22.04 + NVIDIA driver
via SSM Public Parameters, with a per-region in-memory cache (TTL 1 h).

Sync (not async) — used inside Pulumi inline programs that themselves
run synchronously.
"""
from __future__ import annotations

import time
from typing import Dict, Tuple

import boto3
import botocore.exceptions


class AMILookupError(RuntimeError):
    """Raised when the DLAMI SSM parameter is unreachable or missing."""


_DLAMI_PARAMETER = (
    "/aws/service/deeplearning/ami/x86_64/"
    "oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
)
_DLAMI_TTL_S = 3600
_DLAMI_CACHE: Dict[str, Tuple[str, float]] = {}


def latest_dlami_ami(region: str) -> str:
    """Return the latest DLAMI Ubuntu 22.04 + NVIDIA driver AMI for region.

    Per-region cache with a 1 h TTL — the underlying parameter changes
    only on AMI refresh (~monthly).
    """
    now = time.time()
    cached = _DLAMI_CACHE.get(region)
    if cached and (now - cached[1]) < _DLAMI_TTL_S:
        return cached[0]
    ssm = boto3.client("ssm", region_name=region)
    try:
        resp = ssm.get_parameter(Name=_DLAMI_PARAMETER)
    except botocore.exceptions.ClientError as e:
        raise AMILookupError(f"DLAMI lookup failed: {e.response['Error']['Code']}") from e
    except botocore.exceptions.BotoCoreError as e:
        raise AMILookupError(f"DLAMI lookup failed: {type(e).__name__}") from e
    value = resp["Parameter"]["Value"]
    _DLAMI_CACHE[region] = (value, now)
    return value
