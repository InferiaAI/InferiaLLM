"""Catalog values and the quantities derived from them (analytical checks)."""

import pytest

from simulator.classes import PRIORITY, WorkloadClass
from simulator.hardware import get_gpu, get_model, gpu_catalog, model_catalog


def test_kv_bytes_per_token_llama_8b_bf16():
    # 2 (K and V) * 32 layers * 8 KV heads * 128 head_dim * 2 bytes
    assert get_model("llama-3.1-8b").kv_bytes_per_token() == 131_072


def test_kv_bytes_per_token_llama_70b_bf16():
    # 2 * 80 * 8 * 128 * 2
    assert get_model("llama-3.1-70b").kv_bytes_per_token() == 327_680


def test_kv_bytes_per_token_fp8_kv_cache_halves_it():
    assert get_model("llama-3.1-8b").kv_bytes_per_token("fp8") == 65_536


@pytest.mark.parametrize(
    "name, published_billions, rounding",
    # Hugging Face model pages round to 3 significant figures; allow half a unit.
    [("llama-3.1-8b", 8.03, 0.005), ("llama-3.1-70b", 70.6, 0.05)],
)
def test_param_count_matches_published_size(name, published_billions, rounding):
    assert get_model(name).num_params / 1e9 == pytest.approx(published_billions, abs=rounding)


def test_llama_8b_exact_param_count():
    # Hand-computed from config.json: embeddings + 32 layers + final norm + LM head.
    h, inter, vocab = 4096, 14336, 128256
    per_layer = 2 * h * 32 * 128 + 2 * h * 8 * 128 + 3 * h * inter + 2 * h
    assert get_model("llama-3.1-8b").num_params == 32 * per_layer + 2 * vocab * h + h


def test_weight_bytes_bf16():
    m = get_model("llama-3.1-8b")
    assert m.weight_bytes == m.num_params * 2


@pytest.mark.parametrize("entry", list(gpu_catalog().values()) + list(model_catalog().values()))
def test_every_catalog_entry_has_a_source(entry):
    assert entry.source.startswith("https://")
    assert entry.retrieved


def test_h100_values_from_datasheet():
    g = get_gpu("H100-SXM-80GB")
    assert (g.memory_gb, g.hbm_bandwidth_gbps, g.nvlink_gbps) == (80, 3350, 900)
    assert g.peak_flops("bf16") == 989.5e12  # 1,979 with sparsity / 2
    assert g.interconnect_bytes_per_s() == 450e9


def test_pcie_only_gpu_uses_pcie_for_collectives():
    assert get_gpu("L40S-48GB").interconnect_bytes_per_s() == 32e9


def test_unknown_dtype_and_names_are_rejected():
    with pytest.raises(ValueError, match="fp8"):
        get_gpu("A100-SXM-80GB").peak_flops("fp8")
    with pytest.raises(ValueError, match="unknown GPU"):
        get_gpu("B200")
    with pytest.raises(ValueError, match="unknown model"):
        get_model("gpt-5")


def test_workload_classes_match_interfaces_section_3():
    assert {c.value: PRIORITY[c] for c in WorkloadClass} == {
        "realtime_audio": 100,
        "interactive_chat": 80,
        "long_context": 60,
        "embedding": 50,
        "video_job": 30,
        "batch": 10,
    }
