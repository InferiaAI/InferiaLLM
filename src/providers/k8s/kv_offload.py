"""Validated KV-cache tier configuration for the managed vLLM pod path.

Kubernetes allocates CPU memory and mounts storage; it does not implement KV
movement.  This module only renders flags that the pinned vLLM recipe exposes
and rejects a requested tier when the matching runtime wiring is not verified.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


class KvOffloadConfigError(ValueError):
    """A deployment requested a KV tier this provider cannot safely render."""


@dataclass(frozen=True)
class KvCacheConfig:
    """Engine-resident HBM reservation plus optional native CPU KV tier."""

    hbm_utilization: Optional[float] = None
    cpu_offload_gib: Optional[float] = None

    @property
    def enabled(self) -> bool:
        return self.hbm_utilization is not None or self.cpu_offload_gib is not None


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise KvOffloadConfigError(f"{field} must be a positive number")
    return float(value)


def _format(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def from_metadata(
    metadata: Mapping[str, Any],
    *,
    engine: str,
    gpu_allocated: Any,
    ram_gb_allocated: Any,
    custom_command: bool,
) -> KvCacheConfig:
    """Parse the opt-in ``kv_cache`` deployment contract.

    Supported today, for the pinned vLLM image only::

      kv_cache:
        hbm_utilization: 0.90
        cpu_offload: {gib: 16, backend: native}

    The CPU buffer is process memory, so its requested Kubernetes memory must
    contain that buffer plus two GiB of non-KV process headroom.  NVMe tiers
    and LMCache need additional, version-pinned runtime wiring and are refused
    instead of being accepted and ignored.
    """
    raw = metadata.get("kv_cache")
    if raw is None:
        return KvCacheConfig()
    if not isinstance(raw, Mapping):
        raise KvOffloadConfigError("kv_cache must be an object")
    unknown = set(raw) - {"hbm_utilization", "cpu_offload", "nvme"}
    if unknown:
        raise KvOffloadConfigError(f"unsupported kv_cache fields: {sorted(unknown)}")
    if engine != "vllm":
        raise KvOffloadConfigError("kv_cache is currently supported only for direct vLLM deployments")
    if custom_command:
        raise KvOffloadConfigError(
            "kv_cache cannot be combined with command/cmd: InferiaLLM must own vLLM arguments"
        )
    if isinstance(gpu_allocated, bool) or not isinstance(gpu_allocated, int) or gpu_allocated < 1:
        raise KvOffloadConfigError("kv_cache requires at least one allocated GPU")

    hbm = None
    if "hbm_utilization" in raw:
        hbm = _positive_number(raw["hbm_utilization"], "kv_cache.hbm_utilization")
        if hbm > 1:
            raise KvOffloadConfigError("kv_cache.hbm_utilization must be <= 1")

    if raw.get("nvme") is not None:
        raise KvOffloadConfigError(
            "NVMe KV offload is not rendered for the pinned vLLM image yet; "
            "it requires a verified connector version and dedicated volume contract"
        )

    cpu = raw.get("cpu_offload")
    if cpu is None:
        return KvCacheConfig(hbm_utilization=hbm)
    if not isinstance(cpu, Mapping):
        raise KvOffloadConfigError("kv_cache.cpu_offload must be an object")
    if set(cpu) - {"gib", "backend"}:
        raise KvOffloadConfigError("cpu_offload supports only gib and backend")
    backend = str(cpu.get("backend", "native")).lower()
    if backend == "lmcache":
        raise KvOffloadConfigError(
            "LMCache requires a separately pinned server/sidecar and connection contract; "
            "it is not enabled by this vLLM deployment path"
        )
    if backend != "native":
        raise KvOffloadConfigError("cpu_offload.backend must be native")
    cpu_gib = _positive_number(cpu.get("gib"), "kv_cache.cpu_offload.gib")
    if isinstance(ram_gb_allocated, bool) or not isinstance(ram_gb_allocated, (int, float)):
        raise KvOffloadConfigError("ram_gb_allocated must be numeric when CPU KV offload is enabled")
    if float(ram_gb_allocated) < cpu_gib + 2:
        raise KvOffloadConfigError(
            "ram_gb_allocated must reserve CPU KV offload plus 2 GiB process headroom"
        )
    return KvCacheConfig(hbm_utilization=hbm, cpu_offload_gib=cpu_gib)


def vllm_args(config: KvCacheConfig) -> list[str]:
    """Render only the documented native vLLM flags for an accepted config."""
    args: list[str] = []
    if config.hbm_utilization is not None:
        args += ["--gpu-memory-utilization", _format(config.hbm_utilization)]
    if config.cpu_offload_gib is not None:
        args += ["--kv-offloading-size", _format(config.cpu_offload_gib)]
        args += ["--kv-offloading-backend", "native"]
    return args
