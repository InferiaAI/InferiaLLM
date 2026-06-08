"""AMI resolution helpers (DLAMI, plain Ubuntu, and baked engine-cache AMIs).

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
    "base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
)
# Generic Ubuntu 22.04 AMI for CPU-only instances (no NVIDIA driver).
_UBUNTU_PARAMETER = (
    "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
)
_DLAMI_TTL_S = 3600
_DLAMI_CACHE: Dict[str, Tuple[str, float]] = {}

# Tag stamped on baked engine-cache AMIs (see engine_ami_bake.py). resolve_ami
# prefers the newest such AMI over the stock DLAMI for GPU classes.
_ENGINE_CACHE_TAG = "inferia:engine-cache"
_ENGINE_AMI_TTL_S = 300
_ENGINE_AMI_CACHE: Dict[str, Tuple[Optional[str], float]] = {}


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


def _engine_ec2_client(region: str, *, aws_access_key_id=None, aws_secret_access_key=None):
    """boto3 EC2 client for the engine-AMI lookup. Separate seam so tests can
    monkeypatch it without importing boto3 / hitting AWS (mirrors this module's
    credential-threading style)."""
    import boto3

    kwargs = {"region_name": region}
    if aws_access_key_id and aws_secret_access_key:
        kwargs["aws_access_key_id"] = aws_access_key_id
        kwargs["aws_secret_access_key"] = aws_secret_access_key
    return boto3.client("ec2", **kwargs)


def find_engine_ami(
    region: str,
    *,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
) -> Optional[str]:
    """Return the newest available engine-cache AMI in ``region`` (tagged
    ``inferia:engine-cache=true`` and owned by this account), or ``None`` when
    none has been baked. Per-region TTL-cached (300 s) so repeated preflights
    are cheap but a freshly-baked AMI is picked up within minutes."""
    now = time.time()
    cached = _ENGINE_AMI_CACHE.get(region)
    if cached and (now - cached[1]) < _ENGINE_AMI_TTL_S:
        return cached[0]
    try:
        client = _engine_ec2_client(
            region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )
        resp = client.describe_images(
            Owners=["self"],
            Filters=[
                {"Name": f"tag:{_ENGINE_CACHE_TAG}", "Values": ["true"]},
                {"Name": "state", "Values": ["available"]},
            ],
        )
    except Exception:  # noqa: BLE001 — best-effort; never block provisioning
        return None
    images = resp.get("Images") or []
    if not images:
        _ENGINE_AMI_CACHE[region] = (None, now)
        return None
    newest = max(images, key=lambda im: im.get("CreationDate", ""))
    ami_id = newest.get("ImageId")
    _ENGINE_AMI_CACHE[region] = (ami_id, now)
    return ami_id


# Re-exported so callers can switch to the plain Ubuntu image for CPU-only
# instance types (NVIDIA driver isn't useful on a t3.micro).
PLAIN_UBUNTU_PARAMETER = _UBUNTU_PARAMETER


# Instance classes that need an NVIDIA-driver-bearing AMI. Anything not
# in this set falls back to the plain Ubuntu AMI (cheaper, smaller, no
# wasted driver init time on a CPU instance).
_GPU_INSTANCE_CLASSES: frozenset[str] = frozenset({"normal_gpu", "heavy_gpu"})


def resolve_ami(
    *,
    region: str,
    instance_class: str,
    creds: "AWSCredentials | None" = None,  # noqa: F821  (forward ref)
) -> str:
    """Resolve the right AMI for an ``instance_class`` in ``region``.

    GPU classes (``normal_gpu``, ``heavy_gpu``) → latest DLAMI Ubuntu 22.04
    + NVIDIA driver. CPU class → plain Ubuntu 22.04. Used by the
    PreflightHandler so the operator gets an AMI mismatch error within
    seconds rather than after a long ``stack.up()`` retry storm.

    The ``creds`` argument is the AWSCredentials bundle the reconciler
    builds from ProvidersConfig. When None, falls back to the boto3
    default credential chain (useful for tests).
    """
    aws_access_key_id = creds.access_key_id if creds is not None else None
    aws_secret_access_key = creds.secret_access_key if creds is not None else None

    if instance_class in _GPU_INSTANCE_CLASSES:
        engine = find_engine_ami(
            region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )
        if engine:
            return engine

    parameter = (
        _DLAMI_PARAMETER if instance_class in _GPU_INSTANCE_CLASSES
        else _UBUNTU_PARAMETER
    )
    return latest_dlami_ami(
        region,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        parameter_name=parameter,
    )
