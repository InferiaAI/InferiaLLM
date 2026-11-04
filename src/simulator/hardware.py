"""GPU and model catalog, and the quantities derived from it.

The catalog YAML files under ``catalog/`` carry every hardware and model
number with its source. This module loads them into validated specs and
derives what the performance and KV models need (parameter counts, weight
bytes, KV bytes per token) from the architecture, so nothing derived is
hand-copied.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

Dtype = Literal["bf16", "fp16", "fp8"]

DTYPE_BYTES: dict[str, float] = {"bf16": 2.0, "fp16": 2.0, "fp8": 1.0}

GB = 1e9  # datasheets use decimal units


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GpuSpec(_Frozen):
    name: str
    memory_gb: float = Field(gt=0)
    hbm_bandwidth_gbps: float = Field(gt=0)
    peak_tflops: dict[str, float]
    nvlink_gbps: float = Field(ge=0)
    pcie_gbps: float = Field(gt=0)
    source: str
    retrieved: str
    derived: Optional[str] = None

    @property
    def memory_bytes(self) -> float:
        return self.memory_gb * GB

    @property
    def hbm_bytes_per_s(self) -> float:
        return self.hbm_bandwidth_gbps * GB

    def peak_flops(self, dtype: str) -> float:
        if dtype not in self.peak_tflops:
            raise ValueError(f"GPU {self.name} has no catalogued {dtype} peak")
        return self.peak_tflops[dtype] * 1e12

    def interconnect_bytes_per_s(self) -> float:
        """Per-GPU bandwidth for tensor-parallel collectives inside a node.

        NVLink when the part has it, otherwise PCIe. Datasheets quote the
        bidirectional total; a ring all-reduce sends and receives at once,
        so half of it is the usable one-way rate.
        """
        total = self.nvlink_gbps if self.nvlink_gbps > 0 else self.pcie_gbps
        return total * GB / 2


class ModelSpec(_Frozen):
    name: str
    hf_id: str
    num_layers: int = Field(gt=0)
    hidden_size: int = Field(gt=0)
    intermediate_size: int = Field(gt=0)
    num_attention_heads: int = Field(gt=0)
    num_kv_heads: int = Field(gt=0)
    head_dim: int = Field(gt=0)
    vocab_size: int = Field(gt=0)
    tie_word_embeddings: bool
    max_position_embeddings: int = Field(gt=0)
    dtype: Dtype
    source: str
    retrieved: str

    @property
    def num_params(self) -> int:
        """Parameter count of a Llama-style dense decoder.

        Per layer: Q and O projections (hidden x heads*head_dim), K and V
        projections (hidden x kv_heads*head_dim), a gated MLP (3 matrices of
        hidden x intermediate) and two RMSNorm weight vectors. Plus token
        embeddings, a final norm, and the LM head unless it is tied.
        """
        h, d = self.hidden_size, self.head_dim
        attn = 2 * h * self.num_attention_heads * d + 2 * h * self.num_kv_heads * d
        mlp = 3 * h * self.intermediate_size
        per_layer = attn + mlp + 2 * h
        embed = self.vocab_size * h
        head = 0 if self.tie_word_embeddings else self.vocab_size * h
        return self.num_layers * per_layer + embed + head + h

    @property
    def active_params(self) -> int:
        """Parameters touched per token. Dense models: all of them."""
        return self.num_params

    @property
    def dtype_bytes(self) -> float:
        return DTYPE_BYTES[self.dtype]

    @property
    def weight_bytes(self) -> float:
        return self.num_params * self.dtype_bytes

    def kv_bytes_per_token(self, kv_dtype: Optional[str] = None) -> float:
        """K and V for every layer: 2 * layers * kv_heads * head_dim * bytes."""
        b = DTYPE_BYTES[kv_dtype] if kv_dtype else self.dtype_bytes
        return 2 * self.num_layers * self.num_kv_heads * self.head_dim * b


def _load_yaml(name: str) -> dict:
    text = resources.files("simulator.catalog").joinpath(name).read_text("utf-8")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"catalog/{name} must be a mapping")
    return data


@lru_cache(maxsize=None)
def gpu_catalog() -> dict[str, GpuSpec]:
    return {k: GpuSpec(name=k, **v) for k, v in _load_yaml("gpus.yaml")["gpus"].items()}


@lru_cache(maxsize=None)
def model_catalog() -> dict[str, ModelSpec]:
    return {
        k: ModelSpec(name=k, **v) for k, v in _load_yaml("models.yaml")["models"].items()
    }


def get_gpu(name: str) -> GpuSpec:
    try:
        return gpu_catalog()[name]
    except KeyError:
        raise ValueError(
            f"unknown GPU {name!r}; catalogued: {sorted(gpu_catalog())}"
        ) from None


def get_model(name: str) -> ModelSpec:
    try:
        return model_catalog()[name]
    except KeyError:
        raise ValueError(
            f"unknown model {name!r}; catalogued: {sorted(model_catalog())}"
        ) from None
