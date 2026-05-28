"""Providers HTTP endpoints (currently just the AWS instance catalog
for the wizard's instance-type dropdown)."""
from __future__ import annotations

from fastapi import APIRouter

from inferia.services.orchestration.services.adapter_engine.adapters.aws.instance_catalog import (
    INSTANCE_CATALOG,
    InstanceType,
)


router = APIRouter()


def _to_dict(it: InstanceType) -> dict:
    return {
        "name": it.name,
        "cls": it.cls,
        "vcpu": it.vcpu,
        "ram_gb": it.ram_gb,
        "gpu_count": it.gpu_count,
        "gpu_model": it.gpu_model,
        "gpu_ram_gb": it.gpu_ram_gb,
        # Field name on the wire is 'price_per_hour' to match the
        # dashboard contract in apps/dashboard/src/pages/Compute/NewPool.tsx.
        # The Python catalog field stays 'approx_usd_per_hour'; we rename
        # at the serialization boundary.
        "price_per_hour": it.approx_usd_per_hour,
    }


@router.get("/api/v1/providers/aws/instance-catalog")
async def get_aws_instance_catalog() -> dict:
    """Curated EC2 catalog grouped by class. Powers the wizard."""
    grouped: dict[str, list[dict]] = {
        "normal_gpu": [],
        "heavy_gpu": [],
        "cpu": [],
    }
    for it in INSTANCE_CATALOG:
        grouped[it.cls].append(_to_dict(it))
    return grouped
