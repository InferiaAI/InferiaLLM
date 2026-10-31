"""Scenario configuration (docs/platform/interfaces.md §9).

A scenario is a YAML file validated by these Pydantic models. Every model
forbids unknown keys, and options the engine does not implement yet are not
accepted (for example ``router.policy`` only allows ``round_robin`` until the
router slice lands). A scenario therefore never configures behaviour that the
simulator would silently ignore.

Top-level keys: hardware, cluster, models, deployment, router, autoscaler,
admission, workload, failures, run.
"""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from simulator.classes import WorkloadClass
from simulator.hardware import GB, GpuSpec, get_gpu, get_model


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PerfParams(_Strict):
    """Efficiency factors of the roofline model (simulator/perf.py).

    The defaults are UNCALIBRATED placeholders. T5 fits them from GPU-lease
    measurements; until then every result says ``calibration: uncalibrated``.
    """

    mfu: float = Field(0.5, gt=0, le=1)
    hbm_efficiency: float = Field(0.8, gt=0, le=1)
    step_overhead_s: float = Field(0.003, ge=0)
    collective_latency_s: float = Field(1e-5, ge=0)
    activation_reserve_gb: float = Field(2.0, ge=0)
    calibration: str = "uncalibrated"


class HardwareConfig(_Strict):
    perf: PerfParams = PerfParams()


class PoolConfig(_Strict):
    name: str
    gpu: str
    gpus_per_node: int = Field(gt=0)
    nodes: int = Field(gt=0)

    @model_validator(mode="after")
    def _known_gpu(self) -> "PoolConfig":
        get_gpu(self.gpu)
        return self


class ClusterConfig(_Strict):
    pools: list[PoolConfig] = Field(min_length=1)


class DeploymentConfig(_Strict):
    name: str
    model: str
    pool: str
    tensor_parallel: int = Field(1, gt=0)
    replicas: int = Field(1, gt=0)
    # Scenario defaults, not vLLM's (its SchedulerConfig defaults are 128 / 2048 and
    # engine args raise them per usage context; verify for the pinned version).
    max_num_seqs: int = Field(256, gt=0)
    max_num_batched_tokens: int = Field(8192, gt=0)
    max_model_len: int = Field(32768, gt=0)
    block_size: int = Field(16, gt=0)
    gpu_memory_utilization: float = Field(0.92, gt=0, le=1)
    enable_chunked_prefill: bool = True
    enable_prefix_caching: bool = True
    allocator: Literal["paged", "contiguous"] = "paged"
    # What the contiguous allocator reserves per request: the model's maximum
    # length (pre-PagedAttention systems) or the request's own prompt + output.
    contiguous_reserve: Literal["max_model_len", "request"] = "max_model_len"
    preemption: Literal["recompute", "swap"] = "recompute"
    swap_space_gb: float = Field(4.0, ge=0)  # per GPU, CPU RAM

    @model_validator(mode="after")
    def _consistent(self) -> "DeploymentConfig":
        if self.allocator == "contiguous" and self.enable_prefix_caching:
            raise ValueError("prefix caching needs the paged allocator")
        # Both checks mirror vLLM's SchedulerConfig validation
        # (https://docs.vllm.ai/en/latest/api/vllm/config/scheduler/, read 2026-10-31).
        if self.max_num_batched_tokens < self.max_num_seqs:
            raise ValueError("max_num_batched_tokens must be >= max_num_seqs")
        if not self.enable_chunked_prefill and self.max_num_batched_tokens < self.max_model_len:
            raise ValueError(
                "without chunked prefill, max_num_batched_tokens must be >= max_model_len"
            )
        return self


class RouterConfig(_Strict):
    policy: Literal["round_robin"] = "round_robin"


class AutoscalerConfig(_Strict):
    enabled: Literal[False] = False


class FailuresConfig(_Strict):
    enabled: Literal[False] = False


class AdmissionConfig(_Strict):
    # Waiting requests per replica beyond which arrivals are shed (429).
    max_waiting_per_replica: int = Field(4096, gt=0)
    retry_after_s: float = Field(1.0, ge=0)


class FixedDist(_Strict):
    dist: Literal["fixed"]
    value: int = Field(gt=0)


class LognormalDist(_Strict):
    """Lognormal with the given median (= e^mu) and sigma, clipped to [min, max]."""

    dist: Literal["lognormal"]
    median: float = Field(gt=0)
    sigma: float = Field(ge=0)
    min: int = Field(1, gt=0)
    max: int = Field(gt=0)

    @model_validator(mode="after")
    def _bounds(self) -> "LognormalDist":
        if self.min > self.max:
            raise ValueError("min must be <= max")
        return self


TokenDist = Union[FixedDist, LognormalDist]


class SharedPrefix(_Strict):
    """Each request starts with one of ``groups`` shared prefixes (e.g. system prompts)."""

    groups: int = Field(gt=0)
    tokens: int = Field(gt=0)


class ClassMix(_Strict):
    workload_class: WorkloadClass = Field(alias="class")
    weight: float = Field(gt=0)
    deployment: Optional[str] = None
    input_tokens: TokenDist = Field(discriminator="dist")
    output_tokens: TokenDist = Field(discriminator="dist")
    shared_prefix: Optional[SharedPrefix] = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ArrivalConfig(_Strict):
    process: Literal["poisson"] = "poisson"
    rate_rps: float = Field(gt=0)


class WorkloadConfig(_Strict):
    arrival: ArrivalConfig
    classes: list[ClassMix] = Field(min_length=1)


class RunConfig(_Strict):
    seed: int = 1
    duration_s: float = Field(gt=0)
    warmup_s: float = Field(0.0, ge=0)
    drain: bool = True
    max_drain_s: float = Field(3600.0, ge=0)
    slo_ttft_p95_s: Optional[float] = Field(None, gt=0)

    @model_validator(mode="after")
    def _window(self) -> "RunConfig":
        if self.warmup_s >= self.duration_s:
            raise ValueError("warmup_s must be < duration_s")
        return self


class Scenario(_Strict):
    name: str
    description: str = ""
    hardware: HardwareConfig = HardwareConfig()
    cluster: ClusterConfig
    models: list[str] = Field(min_length=1)
    deployment: list[DeploymentConfig] = Field(min_length=1)
    router: RouterConfig = RouterConfig()
    autoscaler: AutoscalerConfig = AutoscalerConfig()
    admission: AdmissionConfig = AdmissionConfig()
    workload: WorkloadConfig
    failures: FailuresConfig = FailuresConfig()
    run: RunConfig

    @model_validator(mode="after")
    def _cross_refs(self) -> "Scenario":
        for m in self.models:
            get_model(m)
        pools = {p.name: p for p in self.cluster.pools}
        if len(pools) != len(self.cluster.pools):
            raise ValueError("pool names must be unique")
        names = [d.name for d in self.deployment]
        if len(set(names)) != len(names):
            raise ValueError("deployment names must be unique")
        used: dict[str, int] = {}
        for d in self.deployment:
            if d.model not in self.models:
                raise ValueError(f"deployment {d.name}: model {d.model!r} not in models")
            if d.pool not in pools:
                raise ValueError(f"deployment {d.name}: unknown pool {d.pool!r}")
            pool = pools[d.pool]
            if d.tensor_parallel > pool.gpus_per_node or pool.gpus_per_node % d.tensor_parallel:
                raise ValueError(
                    f"deployment {d.name}: tensor_parallel must divide gpus_per_node "
                    "(cross-node parallelism is not modelled)"
                )
            used[d.pool] = used.get(d.pool, 0) + d.replicas * d.tensor_parallel
            model = get_model(d.model)
            if d.max_model_len > model.max_position_embeddings:
                raise ValueError(f"deployment {d.name}: max_model_len exceeds the model's maximum")
            blocks = kv_blocks_per_replica(d, get_gpu(pool.gpu), self.hardware.perf)
            if blocks * d.block_size < d.max_model_len:
                raise ValueError(
                    f"deployment {d.name}: KV cache holds {blocks * d.block_size} tokens, "
                    f"less than max_model_len {d.max_model_len} (weights do not leave room)"
                )
        for name, n in used.items():
            cap = pools[name].gpus_per_node * pools[name].nodes
            if n > cap:
                raise ValueError(f"pool {name}: deployments need {n} GPUs, pool has {cap}")
        place_replicas(self)  # mixed TP sizes can still fail to pack without spanning nodes
        for c in self.workload.classes:
            if c.deployment is None:
                if len(self.deployment) != 1:
                    raise ValueError(
                        f"class {c.workload_class.value}: name a deployment (several exist)"
                    )
            elif c.deployment not in names:
                raise ValueError(f"class {c.workload_class.value}: unknown deployment")
        return self

    def deployment_for(self, mix: ClassMix) -> DeploymentConfig:
        name = mix.deployment or self.deployment[0].name
        return next(d for d in self.deployment if d.name == name)

    def pool(self, name: str) -> PoolConfig:
        return next(p for p in self.cluster.pools if p.name == name)


def place_replicas(s: "Scenario") -> list[tuple[DeploymentConfig, str]]:
    """Pack replicas into pool GPUs in deployment order; returns (deployment, node) per replica.

    A replica's TP group starts at a multiple of its TP size, so it never spans
    two nodes (tensor_parallel divides gpus_per_node). Raises ValueError if the
    pool runs out of GPUs.
    """
    cursor: dict[str, int] = {}
    placed: list[tuple[DeploymentConfig, str]] = []
    for dep in s.deployment:
        pool = s.pool(dep.pool)
        tp = dep.tensor_parallel
        for _ in range(dep.replicas):
            start = -(-cursor.get(pool.name, 0) // tp) * tp
            if start + tp > pool.gpus_per_node * pool.nodes:
                raise ValueError(
                    f"pool {pool.name}: cannot place {dep.name} replicas without "
                    "splitting a TP group across nodes; reorder deployments or add GPUs"
                )
            cursor[pool.name] = start + tp
            placed.append((dep, f"{pool.name}-n{start // pool.gpus_per_node}"))
    return placed


def kv_blocks_per_replica(d: DeploymentConfig, gpu: GpuSpec, perf: PerfParams) -> int:
    """KV blocks one replica (a TP group) can hold.

    (HBM * gpu_memory_utilization - activation reserve) per GPU, times TP,
    minus the weights, divided by the bytes one block holds. KV heads are
    sharded across the TP group, so the group's total KV bytes per token is
    the model's full KV bytes per token.
    """
    model = get_model(d.model)
    per_gpu = gpu.memory_bytes * d.gpu_memory_utilization - perf.activation_reserve_gb * GB
    kv_bytes = d.tensor_parallel * per_gpu - model.weight_bytes
    if kv_bytes <= 0:
        return 0
    return int(kv_bytes // (d.block_size * model.kv_bytes_per_token()))


def bundled_scenarios() -> list[str]:
    """Names of the scenario files shipped inside the package."""
    root = resources.files("simulator.scenarios")
    return sorted(p.name[: -len(".yaml")] for p in root.iterdir() if p.name.endswith(".yaml"))


def read_scenario_bytes(ref: str) -> bytes:
    """Read a scenario by path, or by bundled name (``8xH100`` or ``8xH100.yaml``)."""
    path = Path(ref)
    if path.is_file():
        return path.read_bytes()
    name = ref if ref.endswith(".yaml") else f"{ref}.yaml"
    entry = resources.files("simulator.scenarios").joinpath(name)
    if "/" not in ref and entry.is_file():
        return entry.read_bytes()
    raise FileNotFoundError(
        f"no scenario file {ref!r} and no bundled scenario of that name "
        f"(bundled: {', '.join(bundled_scenarios())})"
    )


def parse_scenario(raw: bytes) -> Scenario:
    data = yaml.safe_load(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("a scenario file must be a YAML mapping")
    return Scenario.model_validate(data)


def load_scenario(ref: str) -> tuple[Scenario, str]:
    """Load and validate a scenario. Returns it with the sha256 of the file bytes."""
    raw = read_scenario_bytes(ref)
    return parse_scenario(raw), hashlib.sha256(raw).hexdigest()


def scenario_hash(s: Scenario) -> str:
    """sha256 of the canonical JSON of a scenario built in memory (no source file)."""
    canon = json.dumps(s.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()
