# aws_discovery.py
"""Live AWS account discovery (regions + instance types) for pool creation.

Creds are resolved from the DB-decrypted providers config via the existing
sweep resolver. Every public call raises AwsDiscoveryUnavailable when the
account can't be queried (no creds / AccessDenied / endpoint error) so the
caller can fall back to static data. Results are TTL-cached (monotonic clock)
to keep the pool form responsive."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_REGIONS_TTL_S = 3600
_ITYPES_TTL_S = 24 * 3600
_CACHE: dict[str, tuple[float, object]] = {}


class AwsDiscoveryUnavailable(Exception):
    """Raised when AWS can't be queried (no creds / denied / error)."""


@dataclass
class InstanceTypeInfo:
    instance_type: str
    vcpus: int
    memory_gb: float
    gpu_count: int
    gpu_model: Optional[str]
    is_gpu: bool

    def to_dict(self) -> dict:
        return {
            "instance_type": self.instance_type,
            "vcpus": self.vcpus,
            "memory_gb": self.memory_gb,
            "gpu_count": self.gpu_count,
            "gpu_model": self.gpu_model,
            "is_gpu": self.is_gpu,
        }


async def _resolve_creds() -> Optional[dict]:
    from inferia.services.orchestration.services.adapter_engine.aws_orphan_sweep import (
        resolve_sweep_aws_env, _creds_from_aws_env,
    )
    aws_env = await resolve_sweep_aws_env()
    if not aws_env:
        return None
    return _creds_from_aws_env(aws_env)


def _ec2(region: str, creds: dict):
    import boto3
    return boto3.client("ec2", region_name=region, **creds)


def _cache_get(key: str) -> object | None:
    hit = _CACHE.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    return None


def _cache_put(key: str, value: object, ttl: float) -> None:
    _CACHE[key] = (time.monotonic() + ttl, value)


async def list_regions() -> list[str]:
    cached = _cache_get("regions")
    if cached is not None:
        return cached  # type: ignore[return-value]
    creds = await _resolve_creds()
    if not creds:
        raise AwsDiscoveryUnavailable("no AWS credentials configured")
    try:
        logger.debug("aws_discovery: refreshing regions cache")
        ec2 = _ec2("us-east-1", creds)
        resp = await asyncio.to_thread(ec2.describe_regions, AllRegions=False)
    except Exception as e:  # noqa: BLE001
        raise AwsDiscoveryUnavailable(f"describe_regions failed: {e}") from e
    regions = sorted(r["RegionName"] for r in resp.get("Regions", []) if r.get("RegionName"))
    if not regions:
        raise AwsDiscoveryUnavailable("describe_regions returned no enabled regions")
    _cache_put("regions", regions, _REGIONS_TTL_S)
    return regions
