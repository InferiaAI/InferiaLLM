# aws_discovery.py
"""Live AWS account discovery (regions + instance types) for pool creation.

Creds are resolved from the DB-decrypted providers config via the existing
sweep resolver. Every public call raises AwsDiscoveryUnavailable when the
account can't be queried (no creds / AccessDenied / endpoint error) so the
caller can fall back to static data. Results are TTL-cached (monotonic clock)
to keep the pool form responsive."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_REGIONS_TTL_S = 3600
_ITYPES_TTL_S = 24 * 3600
_PRICES_TTL_S = 24 * 3600
_PRICES_NEG_TTL_S = 600  # negative-cache pricing failures briefly (avoid hammering a missing perm)
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
    gpu_ram_gb: float = 0.0
    price_per_hour: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "instance_type": self.instance_type,
            "vcpus": self.vcpus,
            "memory_gb": self.memory_gb,
            "gpu_count": self.gpu_count,
            "gpu_model": self.gpu_model,
            "is_gpu": self.is_gpu,
            "gpu_ram_gb": self.gpu_ram_gb,
            "price_per_hour": self.price_per_hour,
        }


async def _resolve_creds() -> Optional[dict]:
    from orchestration.adapter_engine.aws_orphan_sweep import (
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


async def list_instance_types(region: str) -> list[InstanceTypeInfo]:
    key = f"itypes:{region}"
    cached = _cache_get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]
    creds = await _resolve_creds()
    if not creds:
        raise AwsDiscoveryUnavailable("no AWS credentials configured")
    try:
        ec2 = _ec2(region, creds)
        names = await asyncio.to_thread(_offered_type_names, ec2, region)
        infos = await asyncio.to_thread(_describe_types, ec2, names)
    except AwsDiscoveryUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        raise AwsDiscoveryUnavailable(f"instance-type discovery failed: {e}") from e
    infos.sort(key=lambda i: (not i.is_gpu, i.instance_type))
    prices = await asyncio.to_thread(_region_price_map, creds, region)
    for info in infos:
        info.price_per_hour = prices.get(info.instance_type)
    _cache_put(key, infos, _ITYPES_TTL_S)
    return infos


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _offered_type_names(ec2, region: str) -> list[str]:
    names: list[str] = []
    paginator = ec2.get_paginator("describe_instance_type_offerings")
    for page in paginator.paginate(
        LocationType="region",
        Filters=[{"Name": "location", "Values": [region]}],
    ):
        names += [o["InstanceType"] for o in page.get("InstanceTypeOfferings", [])]
    return names


def _describe_types(ec2, names: list[str]) -> list[InstanceTypeInfo]:
    out: list[InstanceTypeInfo] = []
    for i in range(0, len(names), 100):  # DescribeInstanceTypes max 100/call
        batch = names[i:i + 100]
        resp = ec2.describe_instance_types(InstanceTypes=batch)
        for it in resp.get("InstanceTypes", []):
            gpus = (it.get("GpuInfo") or {}).get("Gpus") or []
            gpu_count = sum(g.get("Count", 0) for g in gpus)
            gpu_model = gpus[0].get("Name") if gpus else None
            gpu_ram_gb = round(
                _to_float((gpus[0].get("MemoryInfo") or {}).get("SizeInMiB", 0)) / 1024, 1
            ) if gpus else 0.0
            out.append(InstanceTypeInfo(
                instance_type=it["InstanceType"],
                vcpus=(it.get("VCpuInfo") or {}).get("DefaultVCpus", 0),
                memory_gb=round(_to_float((it.get("MemoryInfo") or {}).get("SizeInMiB", 0)) / 1024, 1),
                gpu_count=gpu_count,
                gpu_model=gpu_model,
                is_gpu=gpu_count > 0,
                gpu_ram_gb=gpu_ram_gb,
                price_per_hour=None,
            ))
    return out


def _pricing_client(creds: dict):
    import boto3
    # The Pricing API only has us-east-1 + ap-south-1 endpoints; query any region's
    # prices from us-east-1 via the regionCode filter.
    return boto3.client("pricing", region_name="us-east-1", **creds)


def _region_price_map(creds: dict, region: str) -> dict[str, float]:
    cached = _cache_get(f"prices:{region}")
    if cached is not None:
        return cached  # type: ignore[return-value]
    prices: dict[str, float] = {}
    try:
        client = _pricing_client(creds)
        paginator = client.get_paginator("get_products")
        for page in paginator.paginate(
            ServiceCode="AmazonEC2",
            Filters=[
                {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
                {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
            ],
        ):
            for raw in page.get("PriceList", []):
                try:
                    prod = json.loads(raw)
                    itype = prod["product"]["attributes"]["instanceType"]
                    od = prod["terms"]["OnDemand"]
                    # first OnDemand term → first priceDimension → USD
                    term = next(iter(od.values()))
                    dim = next(iter(term["priceDimensions"].values()))
                    usd = float(dim["pricePerUnit"]["USD"])
                    if usd <= 0:
                        continue
                    if itype not in prices or usd < prices[itype]:
                        prices[itype] = usd
                except (KeyError, StopIteration, ValueError, TypeError):
                    continue
    except Exception as e:  # noqa: BLE001 — pricing is best-effort; never break the list
        logger.info("aws_discovery: pricing lookup failed for %s: %s", region, e)
        _cache_put(f"prices:{region}", {}, _PRICES_NEG_TTL_S)
        return {}
    _cache_put(f"prices:{region}", prices, _PRICES_TTL_S)
    return prices
