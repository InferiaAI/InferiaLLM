"""Roofline model of one engine iteration (a forward pass over a batch).

For a batch where request ``r`` computes ``n_r`` new tokens on top of
``c_r`` tokens already in its KV cache, with T = sum(n_r):

    linear FLOPs    = 2 * active_params * T
    attention FLOPs = 4 * layers * q_heads * head_dim * sum_r (n_r * c_r + n_r * (n_r + 1) / 2)
                      (QK^T and AV, 2 FLOPs per multiply-add each; causal, so the
                      i-th new token attends to c_r + i + 1 positions)
    compute_s       = (linear + attention) / (TP * peak_FLOPs * MFU)

    bytes           = weight_bytes + kv_bytes_per_token * sum_r (c_r + n_r)
                      (weights read once per step; each request's KV context read
                      once and its new tokens' KV written once)
    memory_s        = bytes / (TP * HBM_bandwidth * eta)

    comm_s          = 2 * layers * (2 * (TP - 1) / TP * T * hidden * dtype_bytes / link_BW
                                    + collective_latency)        if TP > 1
                      (two ring all-reduces per layer, after attention and after
                      the MLP, as in Megatron-style tensor parallelism)

    step_s          = max(compute_s, memory_s) + comm_s + step_overhead_s

This is plan WS3's formula with the attention FLOPs and KV writes made
explicit. It ignores the embedding lookup being a gather (it is counted as
a matmul, a small overcount), kernel-level effects (FlashAttention tiling,
CUDA-graph padding), and sampling. MFU, eta and the overheads are fitted
from GPU-lease measurements in T5; until then they are uncalibrated
placeholders and every result says so. GPUs with no calibration data are
"roofline-scaled, uncalibrated".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from simulator.config import PerfParams
from simulator.hardware import GpuSpec, ModelSpec


@dataclass(frozen=True)
class StepCost:
    compute_s: float
    memory_s: float
    comm_s: float
    total_s: float
    tokens: int


class PerfModel:
    def __init__(self, model: ModelSpec, gpu: GpuSpec, tensor_parallel: int, params: PerfParams):
        self.model = model
        self.gpu = gpu
        self.tp = tensor_parallel
        self.params = params
        self._flops = tensor_parallel * gpu.peak_flops(model.dtype) * params.mfu
        self._bw = tensor_parallel * gpu.hbm_bytes_per_s * params.hbm_efficiency
        self._linear_per_token = 2.0 * model.active_params
        self._attn_per_pair = 4.0 * model.num_layers * model.num_attention_heads * model.head_dim
        self._kv_bpt = model.kv_bytes_per_token()
        self._act_per_token = model.hidden_size * model.dtype_bytes
        self._link = gpu.interconnect_bytes_per_s()
        # One-way PCIe per GPU; a TP group moves its shards in parallel.
        self._pcie = tensor_parallel * gpu.pcie_gbps * 1e9 / 2

    def step_time(self, items: Sequence[tuple[int, int]]) -> StepCost:
        """Cost of one forward pass. ``items`` holds (new_tokens, cached_context) per request."""
        if not items:
            raise ValueError("a step needs at least one request")
        tokens = 0
        pairs = 0.0
        kv_tokens = 0
        for n, c in items:
            tokens += n
            pairs += n * c + n * (n + 1) / 2
            kv_tokens += c + n
        compute = (self._linear_per_token * tokens + self._attn_per_pair * pairs) / self._flops
        memory = (self.model.weight_bytes + self._kv_bpt * kv_tokens) / self._bw
        comm = 0.0
        if self.tp > 1:
            per = 2 * (self.tp - 1) / self.tp * tokens * self._act_per_token / self._link
            comm = 2 * self.model.num_layers * (per + self.params.collective_latency_s)
        total = max(compute, memory) + comm + self.params.step_overhead_s
        return StepCost(compute, memory, comm, total, tokens)

    def transfer_time(self, num_bytes: float) -> float:
        """GPU<->CPU copy time (swap) over PCIe, one direction."""
        return num_bytes / self._pcie
