"""Scenario schema: shipped scenarios validate; bad input is rejected."""

import copy

import pytest
import yaml
from pydantic import ValidationError

from simulator.config import (
    bundled_scenarios,
    kv_blocks_per_replica,
    load_scenario,
    parse_scenario,
    read_scenario_bytes,
    scenario_hash,
)
from simulator.hardware import GB, get_gpu, get_model

REQUIRED = {"8xH100", "64xH100", "256xH100", "hetero-h100-a100-l40s", "tiny"}


def _tiny() -> dict:
    return yaml.safe_load(read_scenario_bytes("tiny"))


def _parse(d: dict):
    return parse_scenario(yaml.safe_dump(d).encode())


def test_required_scenarios_are_bundled():
    assert REQUIRED <= set(bundled_scenarios())


@pytest.mark.parametrize("name", sorted(REQUIRED))
def test_bundled_scenarios_validate(name):
    scenario, digest = load_scenario(name)
    assert scenario.name == name
    assert len(digest) == 64


def test_load_by_path_and_hash_is_of_file_bytes(tmp_path):
    import hashlib

    raw = read_scenario_bytes("tiny.yaml")
    f = tmp_path / "s.yaml"
    f.write_bytes(raw)
    _, digest = load_scenario(str(f))
    assert digest == hashlib.sha256(raw).hexdigest()


def test_missing_scenario_lists_bundled_names():
    with pytest.raises(FileNotFoundError, match="tiny"):
        read_scenario_bytes("does-not-exist")
    with pytest.raises(FileNotFoundError):
        read_scenario_bytes("some/dir/tiny")  # a path is never resolved as a bundled name


def test_scenario_must_be_a_mapping():
    with pytest.raises(ValueError, match="mapping"):
        parse_scenario(b"- a\n- b\n")
    with pytest.raises(ValueError, match="mapping"):
        parse_scenario(b"")


def test_scenario_hash_is_stable_and_content_sensitive():
    a = _parse(_tiny())
    d = _tiny()
    d["run"]["seed"] = 2
    assert scenario_hash(a) == scenario_hash(_parse(_tiny()))
    assert scenario_hash(a) != scenario_hash(_parse(d))


def test_kv_blocks_hand_computed():
    s = _parse(_tiny())
    d = s.deployment[0]
    per_gpu = 80 * GB * 0.92 - 2.0 * GB
    expected = int((per_gpu - get_model("llama-3.1-8b").weight_bytes) // (16 * 131_072))
    assert kv_blocks_per_replica(d, get_gpu("H100-SXM-80GB"), s.hardware.perf) == expected == 26_483


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.update(unknown_key=1), "extra"),
        (lambda d: d["router"].update(policy="not-a-policy") if "router" in d else d.update(router={"policy": "not-a-policy"}), "policy"),
        (lambda d: d["deployment"][0].update(model="llama-3.1-70b"), "not in models"),
        (lambda d: d["deployment"][0].update(pool="nope"), "unknown pool"),
        (lambda d: d["deployment"][0].update(tensor_parallel=2), "tensor_parallel"),
        (lambda d: d["deployment"][0].update(replicas=2), "pool h100"),
        (lambda d: d["deployment"][0].update(max_model_len=200_000), "maximum"),
        (lambda d: d["deployment"][0].update(allocator="contiguous"), "paged allocator"),
        (lambda d: d["deployment"][0].update(enable_chunked_prefill=False, max_num_batched_tokens=4096), "chunked prefill"),
        (lambda d: d["cluster"]["pools"][0].update(gpu="B200"), "unknown GPU"),
        (lambda d: d["deployment"][0].update(max_num_seqs=512, max_num_batched_tokens=256), ">= max_num_seqs"),
        (lambda d: d.update(models=["gpt-5"]), "unknown model"),
        (lambda d: d["workload"]["classes"][0].update({"class": "vip"}), "class"),
        (lambda d: d["workload"]["classes"][0].update(deployment="nope"), "unknown deployment"),
        (lambda d: d["workload"]["classes"][0]["input_tokens"].update(min=5000), "min must be"),
        (lambda d: d["run"].update(warmup_s=60), "warmup_s"),
        (lambda d: d["cluster"]["pools"].append(copy.deepcopy(d["cluster"]["pools"][0])), "unique"),
        (lambda d: d["deployment"].append(copy.deepcopy(d["deployment"][0])), "unique"),
    ],
)
def test_invalid_scenarios_are_rejected(mutate, message):
    d = _tiny()
    mutate(d)
    with pytest.raises(ValidationError, match=message):
        _parse(d)


def test_kv_cache_too_small_for_max_model_len_is_rejected():
    d = _tiny()
    d["deployment"][0].update(gpu_memory_utilization=0.23)  # 18.4 GB - 2 GB reserve - 16.06 GB weights ~ 2.6k tokens
    with pytest.raises(ValidationError, match="less than max_model_len"):
        _parse(d)


def test_weights_that_do_not_fit_give_zero_blocks():
    d = _tiny()
    d["models"] = ["llama-3.1-70b"]
    d["deployment"][0]["model"] = "llama-3.1-70b"
    with pytest.raises(ValidationError, match="KV cache holds 0 tokens"):
        _parse(d)


def test_class_must_name_a_deployment_when_several_exist():
    d = _tiny()
    d["cluster"]["pools"][0]["gpus_per_node"] = 2
    second = copy.deepcopy(d["deployment"][0])
    second["name"] = "other"
    d["deployment"].append(second)
    with pytest.raises(ValidationError, match="name a deployment"):
        _parse(d)
    d["workload"]["classes"][0]["deployment"] = "other"
    s = _parse(d)
    assert s.deployment_for(s.workload.classes[0]).name == "other"
