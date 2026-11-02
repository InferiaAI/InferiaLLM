"""Roofline step-time model: simulated times equal the documented formula."""

import pytest

from simulator.config import PerfParams
from simulator.hardware import get_gpu, get_model
from simulator.perf import PerfModel

P = PerfParams(mfu=0.5, hbm_efficiency=0.8, step_overhead_s=0.003, collective_latency_s=1e-5)


def _model(tp=1, model="llama-3.1-8b", gpu="H100-SXM-80GB"):
    return PerfModel(get_model(model), get_gpu(gpu), tp, P)


def test_single_request_prefill_matches_formula():
    m, g = get_model("llama-3.1-8b"), get_gpu("H100-SXM-80GB")
    n = 1000
    linear = 2 * m.num_params * n
    attn = 4 * 32 * 32 * 128 * (n * (n + 1) / 2)  # causal: token i attends to i+1 tokens
    compute = (linear + attn) / (989.5e12 * 0.5)
    memory = (m.weight_bytes + 131_072 * n) / (3350e9 * 0.8)
    cost = _model().step_time([(n, 0)])
    assert cost.compute_s == pytest.approx(compute)
    assert cost.memory_s == pytest.approx(memory)
    assert cost.comm_s == 0
    assert cost.total_s == pytest.approx(max(compute, memory) + 0.003)
    assert cost.total_s > cost.memory_s  # a 1k-token prefill on an 8B model is compute-bound


def test_single_request_decode_matches_formula_and_is_memory_bound():
    m = get_model("llama-3.1-8b")
    ctx = 1000
    compute = (2 * m.num_params + 4 * 32 * 32 * 128 * (ctx + 1)) / (989.5e12 * 0.5)
    memory = (m.weight_bytes + 131_072 * (ctx + 1)) / (3350e9 * 0.8)
    cost = _model().step_time([(1, ctx)])
    assert cost.compute_s == pytest.approx(compute)
    assert cost.memory_s == pytest.approx(memory)
    assert cost.total_s == pytest.approx(memory + 0.003)


def test_chunked_prefill_attention_counts_the_existing_context():
    # Second 512-token chunk of a 1024-token prompt: positions 512..1023.
    n, c = 512, 512
    pairs = n * c + n * (n + 1) / 2
    m = get_model("llama-3.1-8b")
    cost = _model().step_time([(n, c)])
    assert cost.compute_s == pytest.approx((2 * m.num_params * n + 4 * 32 * 32 * 128 * pairs) / (989.5e12 * 0.5))


def test_batch_sums_tokens_and_kv_reads():
    m = get_model("llama-3.1-8b")
    items = [(1, 100), (1, 300), (64, 0)]
    cost = _model().step_time(items)
    kv = 131_072 * (101 + 301 + 64)
    assert cost.memory_s == pytest.approx((m.weight_bytes + kv) / (3350e9 * 0.8))
    assert cost.tokens == 66


def test_tensor_parallel_splits_work_and_adds_allreduce():
    m = get_model("llama-3.1-70b")
    tp, n = 4, 256
    cost = _model(tp=tp, model="llama-3.1-70b").step_time([(n, 0)])
    act = n * 8192 * 2
    per_allreduce = 2 * (tp - 1) / tp * act / 450e9 + 1e-5
    assert cost.comm_s == pytest.approx(2 * 80 * per_allreduce)
    linear = 2 * m.num_params * n
    attn = 4 * 80 * 64 * 128 * (n * (n + 1) / 2)
    assert cost.compute_s == pytest.approx((linear + attn) / (tp * 989.5e12 * 0.5))
    assert cost.total_s == pytest.approx(max(cost.compute_s, cost.memory_s) + cost.comm_s + 0.003)


def test_empty_batch_is_rejected():
    with pytest.raises(ValueError):
        _model().step_time([])


def test_swap_time_uses_one_way_pcie():
    # 10 blocks of 16 tokens of Llama-3.1-8B over PCIe Gen5 (128 GB/s bidirectional -> 64 one way)
    assert _model().transfer_time(10 * 16 * 131_072) == pytest.approx(10 * 16 * 131_072 / 64e9)
