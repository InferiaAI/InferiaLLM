"""Curated AWS EC2 instance-type catalog used by the New Pool wizard
and the PreflightHandler.

This is intentionally a static module rather than a live AWS pricing
fetch — the dashboard needs a snappy /providers/aws/instance-catalog
response, and approximate prices are accurate enough for UX purposes.
Add an entry here when you want a new type to show up in the wizard."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


InstanceClass = Literal["normal_gpu", "heavy_gpu", "cpu"]


@dataclass(frozen=True)
class InstanceType:
    """One row in the curated catalog."""

    name: str                       # e.g. 'g6.xlarge'
    cls: InstanceClass              # tier the wizard groups this under
    vcpu: int
    ram_gb: int
    gpu_count: int                  # 0 for cpu
    gpu_model: str | None           # None for cpu
    gpu_ram_gb: int                 # 0 for cpu
    # NOTE: serialized as 'price_per_hour' on the HTTP catalog endpoint
    # (T22) to match the existing dashboard contract in
    # apps/dashboard/src/pages/Compute/NewPool.tsx.
    approx_usd_per_hour: float


# Curated initial catalog. Prices are approximate us-east-1 on-demand
# values from AWS public pricing as of 2026-05; close enough for UX.
INSTANCE_CATALOG: list[InstanceType] = [
    # --- normal_gpu: single-GPU inference (7-13B, 24 GB VRAM) ----------
    InstanceType("g5.xlarge",   "normal_gpu",  4,  16, 1, "NVIDIA A10G",  24, 1.006),
    InstanceType("g5.2xlarge",  "normal_gpu",  8,  32, 1, "NVIDIA A10G",  24, 1.212),
    InstanceType("g5.4xlarge",  "normal_gpu", 16,  64, 1, "NVIDIA A10G",  24, 1.624),
    InstanceType("g6.xlarge",   "normal_gpu",  4,  16, 1, "NVIDIA L4",    24, 0.805),
    InstanceType("g6.2xlarge",  "normal_gpu",  8,  32, 1, "NVIDIA L4",    24, 0.978),
    InstanceType("g6.4xlarge",  "normal_gpu", 16,  64, 1, "NVIDIA L4",    24, 1.323),
    # --- heavy_gpu: multi-GPU / large model inference -----------------
    InstanceType("g5.12xlarge", "heavy_gpu",  48, 192, 4, "NVIDIA A10G",  96, 5.672),
    InstanceType("g5.48xlarge", "heavy_gpu", 192, 768, 8, "NVIDIA A10G", 192, 16.288),
    InstanceType("g6.12xlarge", "heavy_gpu",  48, 192, 4, "NVIDIA L4",    96, 4.602),
    InstanceType("p4d.24xlarge","heavy_gpu",  96,1152, 8, "NVIDIA A100", 320, 32.770),
    InstanceType("p4de.24xlarge","heavy_gpu", 96,1152, 8, "NVIDIA A100", 640, 40.965),
    InstanceType("p5.48xlarge", "heavy_gpu", 192,2048, 8, "NVIDIA H100", 640, 98.320),
    # --- cpu: quantized small models, embeddings, cheap test pools ----
    InstanceType("c6i.xlarge",  "cpu",  4,   8, 0, None, 0, 0.170),
    InstanceType("c6i.2xlarge", "cpu",  8,  16, 0, None, 0, 0.340),
    InstanceType("c6i.4xlarge", "cpu", 16,  32, 0, None, 0, 0.680),
    InstanceType("m6i.xlarge",  "cpu",  4,  16, 0, None, 0, 0.192),
    InstanceType("m6i.2xlarge", "cpu",  8,  32, 0, None, 0, 0.384),
    InstanceType("m6i.4xlarge", "cpu", 16,  64, 0, None, 0, 0.768),
]


_BY_NAME: dict[str, InstanceType] = {it.name: it for it in INSTANCE_CATALOG}


def lookup(name: str) -> InstanceType | None:
    """Return the catalog entry for `name`, or None if not in the catalog."""
    return _BY_NAME.get(name)


def by_class(cls: str) -> list[InstanceType]:
    """All catalog entries belonging to `cls`. Unknown class → []."""
    return [it for it in INSTANCE_CATALOG if it.cls == cls]
