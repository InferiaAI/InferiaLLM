"""Engine invariants and behaviour on small scenarios.

Invariants (plan WS3 "Validation"): request conservation, KV block
conservation at every step, Little's law at steady state, determinism.
Behaviour: saturation is handled by preemption / admission, never by a crash
or a stuck request; priorities, prefix caching and allocators have the
expected effect.
"""

import json

import pytest
import yaml

from simulator.classes import WorkloadClass
from simulator.config import parse_scenario, read_scenario_bytes
from simulator.engine import Simulation
from simulator.metrics import LogHistogram, percentile
from simulator.perf import PerfModel
from simulator.hardware import get_gpu, get_model
from simulator.report import summarize
from simulator.workload import Request


def scenario(**overrides):
    """The bundled tiny scenario, with nested overrides like deployment={...}."""
    d = yaml.safe_load(read_scenario_bytes("tiny"))
    for key, value in overrides.items():
        if key == "deployment":
            d["deployment"][0].update(value)
        elif key == "classes":
            d["workload"]["classes"] = value
        elif key in ("run", "admission"):
            d.setdefault(key, {}).update(value)
        elif key == "rate":
            d["workload"]["arrival"]["rate_rps"] = value
        elif key == "pool":
            d["cluster"]["pools"][0].update(value)
        else:
            d[key] = value
    return parse_scenario(yaml.safe_dump(d).encode())


def chat(inp, out, prefix=None, cls="interactive_chat", weight=1):
    c = {
        "class": cls,
        "weight": weight,
        "input_tokens": inp if isinstance(inp, dict) else {"dist": "fixed", "value": inp},
        "output_tokens": out if isinstance(out, dict) else {"dist": "fixed", "value": out},
    }
    if prefix:
        c["shared_prefix"] = prefix
    return c


def run(s, check=None, **kw):
    """Run with the per-step KV invariant check on small caches.

    The check is O(blocks) per step, so on a full-size cache (26k blocks) it
    dominates runtime; the saturation tests use ~540-block caches, where it is
    cheap and where preemption actually stresses the manager.
    """
    sim = Simulation(s, check_invariants=False, **kw)
    if check is None:
        check = all(r.kv.num_blocks <= 2048 for r in sim.replicas)
    sim.check = check
    result = sim.run()
    return result, summarize(result, s, config_hash="test", command="test")


def accounted(t):
    return t["completed"] + t["failed"] + t["shed"] + t["cancelled"] + t["unfinished"]


# --- invariants ------------------------------------------------------------------------


def test_request_and_kv_conservation_on_tiny():
    # Small cache so the per-step invariant check runs and preemption occurs.
    result, summ = run(scenario(deployment={"gpu_memory_utilization": 0.24, "max_model_len": 2048},
                                rate=3, run={"duration_s": 40, "warmup_s": 5}))
    t = summ["totals"]
    assert t["requests"] > 50
    assert accounted(t) == t["requests"]
    assert t["unfinished"] == 0  # drained
    assert result.invariant_checks > 0  # KV conservation was asserted at every step
    assert t["preempted"] > 0


def test_not_draining_leaves_unfinished_but_still_accounts_for_everything():
    _, summ = run(scenario(rate=20, run={"drain": False}))
    t = summ["totals"]
    assert t["unfinished"] > 0
    assert accounted(t) == t["requests"]


def test_determinism_same_seed_identical_summary(monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")
    a = json.dumps(run(scenario())[1], sort_keys=True)
    b = json.dumps(run(scenario())[1], sort_keys=True)
    assert a == b


def test_different_seed_changes_the_run():
    _, a = run(scenario())
    _, b = run(scenario(), seed=2)
    assert a["totals"]["requests"] != b["totals"]["requests"] or a["latency"] != b["latency"]
    assert b["manifest"]["seed"] == 2


def test_queue_autoscaler_activates_warm_replica():
    s = scenario(
        deployment={"replicas": 2},
        pool={"nodes": 2},
        rate=80,
        autoscaler={"enabled": True, "target": 1, "interval_s": 0.1,
                    "min_replicas": 1, "max_replicas": 2, "warm_pool": 0,
                    "warm_start_s": 0.01, "cold_start_s": 10},
        run={"duration_s": 2, "warmup_s": 0},
    )
    sim = Simulation(s)
    assert sim._active == {0}
    sim.run()
    assert sim._active == {0, 1}


@pytest.mark.parametrize("metric,target", [("kv_utilization", 0.001), ("ttft_p95", 0.0001)])
def test_autoscaler_supports_kv_and_ttft_signals(metric, target):
    s = scenario(
        deployment={"replicas": 2}, pool={"nodes": 2}, rate=80,
        autoscaler={"enabled": True, "metric": metric, "target": target, "interval_s": 0.01,
                    "cooldown_s": 0, "min_replicas": 1, "max_replicas": 2,
                    "warm_start_s": 0.001, "cold_start_s": 0.001},
        run={"duration_s": 1, "warmup_s": 0},
    )
    sim = Simulation(s)
    sim.run()
    assert sim._active == {0, 1}


def test_autoscaler_cooldown_limits_repeated_scale_out():
    s = scenario(
        deployment={"replicas": 3}, pool={"nodes": 3}, rate=80,
        autoscaler={"enabled": True, "target": 1, "interval_s": 0.01, "cooldown_s": 10,
                    "min_replicas": 1, "max_replicas": 3,
                    "warm_start_s": 0.001, "cold_start_s": 0.001},
        run={"duration_s": 1, "warmup_s": 0},
    )
    sim = Simulation(s)
    sim.run()
    assert sim._active == {0, 1}


def test_replica_failure_retries_only_a_request_without_a_first_token():
    s = scenario(
        deployment={"replicas": 2}, pool={"nodes": 2},
        failures={"enabled": True, "retry_max_attempts": 1, "retry_budget_fraction": 1,
                  "retry_backoff_s": 0.01,
                  "events": [{"at_s": 0.001, "scope": "replica", "target": 0}]},
    )
    req = Request(rid=1, arrival=0.0, workload_class=WorkloadClass.INTERACTIVE_CHAT,
                  prompt_segments=[(2, 2000)], output_len=2, deployment="chat-8b")
    result, summ = run(s, requests=[req])
    rec = result.records[0]
    assert rec.status == "completed"
    assert rec.retries == 1
    assert rec.replica == 1
    assert summ["totals"]["completed"] == 1


def test_failure_after_first_token_is_not_retried_and_recovery_restores_replica():
    s = scenario(
        failures={"enabled": True, "retry_max_attempts": 1, "retry_budget_fraction": 1,
                  "events": [{"at_s": 0.02, "scope": "replica", "target": 0,
                              "recover_after_s": 0.01}]},
    )
    req = Request(rid=1, arrival=0.0, workload_class=WorkloadClass.INTERACTIVE_CHAT,
                  prompt_segments=[(2, 10)], output_len=100, deployment="chat-8b")
    sim = Simulation(s, requests=[req])
    result = sim.run()
    assert result.records[0].status == "failed"
    assert result.records[0].reason == "replica_failure"
    assert result.records[0].retries == 0
    assert sim._failed == set()


def test_node_and_zone_failure_targets_are_validated():
    with pytest.raises(ValueError, match="node failure target"):
        scenario(failures={"enabled": True, "events": [{"at_s": 1, "scope": "node", "target": "gone"}]})
    with pytest.raises(ValueError, match="fraction failure"):
        scenario(failures={"enabled": True, "events": [{"at_s": 1, "scope": "fraction"}]})


def test_littles_law_exact_over_a_drained_horizon_and_close_at_steady_state():
    _, summ = run(scenario(rate=8, run={"duration_s": 400, "warmup_s": 100}))
    full = summ["littles_law"]["full_horizon"]
    assert full["L"] == pytest.approx(full["lambda"] * full["W"], rel=1e-9)
    win = summ["littles_law"]["window"]
    assert win["L"] == pytest.approx(win["lambda"] * win["W"], rel=0.05)


# --- analytical: one request -------------------------------------------------------------


def test_single_request_times_equal_the_perf_model():
    s = scenario()
    req = Request(rid=1, arrival=0.0, workload_class=WorkloadClass.INTERACTIVE_CHAT,
                  prompt_segments=[(2, 1000)], output_len=5, deployment="chat-8b")
    result, summ = run(s, requests=[req])
    rec = result.records[0]
    pm = PerfModel(get_model("llama-3.1-8b"), get_gpu("H100-SXM-80GB"), 1, s.hardware.perf)
    prefill = pm.step_time([(1000, 0)]).total_s
    decodes = [pm.step_time([(1, 1000 + i)]).total_s for i in range(4)]
    assert rec.ttft == pytest.approx(prefill)
    assert rec.e2e == pytest.approx(prefill + sum(decodes))
    assert rec.queue == 0
    assert summ["totals"]["completed"] == 1


def test_chunked_prefill_splits_a_long_prompt():
    s = scenario(deployment={"max_num_batched_tokens": 512})
    req = Request(rid=1, arrival=0.0, workload_class=WorkloadClass.INTERACTIVE_CHAT,
                  prompt_segments=[(2, 1200)], output_len=1, deployment="chat-8b")
    result, _ = run(s, requests=[req])
    pm = PerfModel(get_model("llama-3.1-8b"), get_gpu("H100-SXM-80GB"), 1, s.hardware.perf)
    expected = sum(pm.step_time([(n, c)]).total_s for n, c in [(512, 0), (512, 512), (176, 1024)])
    assert result.records[0].ttft == pytest.approx(expected)


# --- admission and failure handling ---------------------------------------------------------


def test_prompt_longer_than_max_model_len_is_rejected_not_stuck():
    s = scenario(classes=[chat(9000, 10)])
    _, summ = run(s)
    t = summ["totals"]
    assert t["completed"] == 0 and t["failed"] == t["requests"] > 0
    assert summ["failure_reasons"] == {"context_length_exceeded": t["failed"]}


def test_max_model_len_boundary():
    # Config validation guarantees the KV cache holds max_model_len tokens, so the
    # context-length check is the only size rejection that can happen.
    fits = scenario(classes=[chat(8192 - 100, 100)], rate=0.5)
    _, summ = run(fits)
    assert summ["totals"]["completed"] == summ["totals"]["requests"] > 0
    over = scenario(classes=[chat(8192 - 100, 101)], rate=0.5)
    _, summ = run(over)
    assert summ["failure_reasons"] == {"context_length_exceeded": summ["totals"]["requests"]}


def test_queue_cap_sheds_with_retry_after():
    s = scenario(rate=50, admission={"max_waiting_per_replica": 4}, classes=[chat(2000, 400)],
                 run={"duration_s": 30, "warmup_s": 5})
    _, summ = run(s)
    t = summ["totals"]
    assert t["shed"] > 0
    assert accounted(t) == t["requests"]
    assert summ["admission"]["retry_after_s"] == 1.0


# --- KV saturation ------------------------------------------------------------------------------


def _pressure(**dep):
    # ~0.6 GB of KV (~4.6k tokens) for many concurrent 1k-token requests.
    base = {"gpu_memory_utilization": 0.24, "max_model_len": 2048}
    base.update(dep)
    return scenario(deployment=base, rate=10, classes=[chat(600, 400)],
                    run={"duration_s": 60, "warmup_s": 10})


def test_saturation_preempts_by_recompute_and_completes_everything():
    _, summ = run(_pressure())
    t = summ["totals"]
    assert t["preempted"] > 0
    assert t["unfinished"] == 0 and t["failed"] == 0
    assert t["completed"] == t["requests"]
    assert summ["kv"]["utilization_p95"] > 0.9


def test_swap_preemption_moves_blocks_to_cpu():
    _, summ = run(_pressure(preemption="swap"))
    assert summ["kv"]["swapped_out_blocks"] > 0
    assert summ["totals"]["completed"] == summ["totals"]["requests"]


def test_swap_without_cpu_space_falls_back_to_recompute():
    _, summ = run(_pressure(preemption="swap", swap_space_gb=0))
    assert summ["kv"]["swapped_out_blocks"] == 0
    assert summ["totals"]["preempted"] > 0
    assert summ["totals"]["completed"] == summ["totals"]["requests"]


def test_explicit_offload_tier_sets_capacity_and_charges_its_bandwidth():
    fast = _pressure(preemption="swap", offload={"tier": "cpu", "capacity_gb": 2,
                                                   "bandwidth_gbps": 400})
    slow = _pressure(preemption="swap", offload={"tier": "nvme", "capacity_gb": 2,
                                                   "bandwidth_gbps": 8})
    fast_result, fast_summary = run(fast)
    slow_result, slow_summary = run(slow)
    assert fast_result.replicas[0].kv.cpu_num_blocks == int(2 * 1_000_000_000 // fast_result.replicas[0].block_bytes)
    assert fast_summary["kv"]["swapped_out_blocks"] > 0
    assert slow_summary["kv"]["swapped_out_blocks"] > 0
    assert slow_result.end_time > fast_result.end_time


def test_offload_configuration_requires_swap_preemption():
    with pytest.raises(ValueError, match="requires preemption"):
        _pressure(offload={"tier": "cpu", "capacity_gb": 1})


def test_lower_priority_class_is_the_preemption_victim():
    # ~8.7k tokens of KV. A batch request starts first, then 8 chat requests join.
    # Together they outgrow the cache; the batch request must be preempted, and once
    # it is gone the 8 chat requests (8 x 63 blocks) fit, so none of them is.
    s = _pressure()
    reqs = [Request(rid=0, arrival=0.0, workload_class=WorkloadClass.BATCH,
                    prompt_segments=[(0, 600)], output_len=1000, deployment="chat-8b")]
    reqs += [Request(rid=i, arrival=0.5, workload_class=WorkloadClass.INTERACTIVE_CHAT,
                     prompt_segments=[(3 * i, 600)], output_len=400, deployment="chat-8b")
             for i in range(1, 9)]
    result, summ = run(s, requests=reqs)
    by_class = {r.rid: r.preemptions for r in result.records}
    assert by_class[0] >= 1
    assert all(by_class[i] == 0 for i in range(1, 9))
    assert summ["totals"]["completed"] == 9


def test_interactive_waits_less_than_batch_under_backlog():
    s = scenario(deployment={"gpu_memory_utilization": 0.24, "max_model_len": 2048}, rate=6,
                 classes=[chat(600, 400, cls="interactive_chat"), chat(600, 400, cls="batch")],
                 run={"duration_s": 40, "warmup_s": 5})
    result, _ = run(s)
    def p50_queue(c):
        qs = sorted(r.queue for r in result.records if r.workload_class == c and r.queue is not None)
        return qs[len(qs) // 2]
    assert p50_queue(WorkloadClass.INTERACTIVE_CHAT) * 5 < p50_queue(WorkloadClass.BATCH)


def test_prefix_caching_hits_and_lowers_ttft():
    prefix = {"groups": 2, "tokens": 1024}
    on = scenario(classes=[chat(64, 16, prefix=prefix)], rate=4)
    off = scenario(classes=[chat(64, 16, prefix=prefix)], rate=4,
                   deployment={"enable_prefix_caching": False})
    _, s_on = run(on)
    _, s_off = run(off)
    assert s_on["kv"]["prefix_cache_hit_ratio"] > 0.8
    assert s_off["kv"]["prefix_cache_hit_ratio"] == 0
    assert s_on["latency"]["ttft_seconds"]["p50"] < s_off["latency"]["ttft_seconds"]["p50"]


def test_contiguous_allocation_wastes_memory_and_queues_more():
    common = dict(rate=10, classes=[chat(300, 100)], run={"duration_s": 60, "warmup_s": 10})
    paged = scenario(deployment={"gpu_memory_utilization": 0.24, "max_model_len": 2048}, **common)
    contig = scenario(deployment={"gpu_memory_utilization": 0.24, "max_model_len": 2048,
                                  "allocator": "contiguous", "enable_prefix_caching": False}, **common)
    _, sp = run(paged)
    _, sc = run(contig)
    assert sc["kv"]["internal_waste_ratio_mean"] > sp["kv"]["internal_waste_ratio_mean"]
    assert sc["latency"]["queue_seconds"]["p95"] > sp["latency"]["queue_seconds"]["p95"]
    assert sc["totals"]["completed"] == sc["totals"]["requests"]
    assert "external_fragmentation_mean" in sc["kv"]


# --- multi-replica and summary shape ------------------------------------------------------------


def test_round_robin_spreads_requests_over_replicas():
    d = yaml.safe_load(read_scenario_bytes("tiny"))
    d["cluster"]["pools"][0]["gpus_per_node"] = 4
    d["deployment"][0]["replicas"] = 4
    s = parse_scenario(yaml.safe_dump(d).encode())
    result, summ = run(s)
    counts = [sum(1 for r in result.records if r.replica == i) for i in range(4)]
    assert max(counts) - min(counts) <= 1
    assert len(summ["per_node"]) == 1 and summ["per_node"][0]["replicas"] == 4


def test_summary_matches_interfaces_section_9_shape():
    _, summ = run(scenario())
    for key in ("manifest", "scenario", "totals", "throughput", "latency", "kv", "gpu", "per_node", "slo", "artifacts"):
        assert key in summ
    for k in ("ttft_seconds", "itl_seconds", "e2e_seconds", "queue_seconds"):
        assert set(summ["latency"][k]) >= {"p50", "p95", "p99"}
    m = summ["manifest"]
    assert m["label"] == "SIMULATED" and m["schema_version"] == "1"
    assert m["tool"]["name"] == "inferia-sim"
    assert "uncalibrated" in m["label_detail"]
    assert summ["slo"]["ttft_p95_target_s"] == 1.0 and isinstance(summ["slo"]["met"], bool)
    assert 0 < summ["gpu"]["utilization_mean"] <= 1
    assert summ["throughput"]["output_tokens_per_second"] > 0


# --- metric helpers ---------------------------------------------------------------------------


def test_percentile_linear_interpolation():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([5], 99) == 5
    assert percentile([], 50) is None
    assert percentile([1, 2, 3, 4, 5], 100) == 5


def test_log_histogram_quantiles_within_one_percent():
    h = LogHistogram()
    values = [0.001 * (i + 1) for i in range(1000)]
    for v in values:
        h.add(v)
    h.add(0.5, count=0)
    assert h.quantile(0.5) == pytest.approx(0.5, rel=0.01)
    assert h.quantile(0.99) == pytest.approx(0.99, rel=0.01)
    assert LogHistogram().quantile(0.5) is None
    h.add(0.0)  # zero-length intervals land in the lowest bucket
    assert h.count == 1001


def test_without_chunked_prefill_a_prompt_runs_in_one_step_and_the_next_waits():
    s = scenario(deployment={"enable_chunked_prefill": False, "max_num_batched_tokens": 8192})
    reqs = [Request(rid=i, arrival=0.0, workload_class=WorkloadClass.INTERACTIVE_CHAT,
                    prompt_segments=[(3 * i, 5000)], output_len=2, deployment="chat-8b") for i in (1, 2)]
    result, _ = run(s, requests=reqs)
    pm = PerfModel(get_model("llama-3.1-8b"), get_gpu("H100-SXM-80GB"), 1, s.hardware.perf)
    first = pm.step_time([(5000, 0)]).total_s
    r1, r2 = result.records
    assert r1.ttft == pytest.approx(first)
    # Step 2: request 1 decodes (decodes go first), request 2's 5000 tokens fit in 8191.
    assert r2.ttft == pytest.approx(first + pm.step_time([(1, 5000), (5000, 0)]).total_s)


def test_decodes_are_batched_before_a_running_prefill():
    # Budget 512: request 1 is mid-prefill (4096 tokens) when request 2 starts decoding.
    s = scenario(deployment={"max_num_batched_tokens": 512, "max_num_seqs": 64})
    reqs = [Request(rid=2, arrival=0.0, workload_class=WorkloadClass.INTERACTIVE_CHAT,
                    prompt_segments=[(6, 100)], output_len=20, deployment="chat-8b"),
            Request(rid=1, arrival=0.001, workload_class=WorkloadClass.INTERACTIVE_CHAT,
                    prompt_segments=[(3, 4096)], output_len=2, deployment="chat-8b")]
    result, _ = run(s, requests=reqs)
    short = next(r for r in result.records if r.rid == 2)
    # Without decode-first, the 512-token prefill chunks would take the whole budget
    # and the short request would stall for 8 steps. Every gap is one step.
    pm = PerfModel(get_model("llama-3.1-8b"), get_gpu("H100-SXM-80GB"), 1, s.hardware.perf)
    one_big_step = pm.step_time([(1, 200), (511, 4000)]).total_s
    assert short.e2e - short.ttft < 19 * one_big_step


def test_tiny_token_budget_never_deadlocks():
    s = scenario(deployment={"max_num_batched_tokens": 16, "max_num_seqs": 16, "max_model_len": 2048,
                             "gpu_memory_utilization": 0.24},
                 classes=[chat(40, 30)], rate=20, run={"duration_s": 10, "warmup_s": 1})
    _, summ = run(s)
    t = summ["totals"]
    assert t["completed"] == t["requests"] > 100


def test_replicas_with_mixed_tp_are_placed_without_spanning_nodes():
    d = yaml.safe_load(read_scenario_bytes("tiny"))
    d["cluster"]["pools"][0].update(gpus_per_node=8, nodes=2)
    base = d["deployment"][0]
    d["deployment"] = [dict(base, name="a", tensor_parallel=1, replicas=1),
                       dict(base, name="b", tensor_parallel=4, replicas=3)]
    d["workload"]["classes"][0]["deployment"] = "b"
    sim = Simulation(parse_scenario(yaml.safe_dump(d).encode()))
    assert [r.node for r in sim.replicas] == ["h100-n0", "h100-n0", "h100-n1", "h100-n1"]
    d["deployment"].append(dict(base, name="c", tensor_parallel=2, replicas=1))  # 15 of 16 GPUs
    with pytest.raises(ValueError, match="splitting a TP group"):
        parse_scenario(yaml.safe_dump(d).encode())


def test_scheduler_stall_is_reported_not_hidden(monkeypatch):
    from simulator.scheduler import Replica

    monkeypatch.setattr(Replica, "schedule", lambda self, now: None)
    with pytest.raises(RuntimeError, match="stalled"):
        run(scenario())


def test_request_validation():
    with pytest.raises(ValueError):
        Request(rid=1, arrival=0, workload_class=WorkloadClass.BATCH, prompt_segments=[(3, 4)],
                output_len=0, deployment="x")
    with pytest.raises(ValueError):
        Request(rid=1, arrival=0, workload_class=WorkloadClass.BATCH, prompt_segments=[(3, 0)],
                output_len=1, deployment="x")


def test_git_metadata_override_and_failure(monkeypatch):
    import subprocess

    from simulator import report

    monkeypatch.setenv("INFERIA_GIT_SHA", "abc123")
    assert report._git() == ("abc123", None)
    monkeypatch.delenv("INFERIA_GIT_SHA")

    def boom(*a, **k):
        raise OSError("no git")

    monkeypatch.setattr(subprocess, "run", boom)
    assert report._git() == ("unknown", None)


def test_weighted_bins_empty():
    from simulator.metrics import WeightedBins

    assert WeightedBins().quantile(0.5) == 0.0 and WeightedBins().mean() == 0.0


def test_without_chunking_a_full_cache_blocks_admission_until_space_frees():
    # ~352 blocks (5.6k tokens): two 1900-token prompts fit, the third must wait for KV,
    # not for the token budget (8192).
    s = scenario(deployment={"enable_chunked_prefill": False, "gpu_memory_utilization": 0.235,
                             "max_model_len": 2048})
    reqs = [Request(rid=i, arrival=0.0, workload_class=WorkloadClass.INTERACTIVE_CHAT,
                    prompt_segments=[(3 * i, 1900)], output_len=40, deployment="chat-8b") for i in range(1, 4)]
    result, summ = run(s, requests=reqs)
    r1, r2, r3 = result.records
    assert r1.first_scheduled == r2.first_scheduled == 0.0
    assert r3.first_scheduled > 0.0
    assert summ["totals"]["completed"] == 3


def test_gpu_utilization_is_bound_time_over_wall_time():
    # One request, no warmup: utilisation = max(compute, memory) of each step / window.
    s = scenario(run={"duration_s": 10, "warmup_s": 0})
    req = Request(rid=1, arrival=0.0, workload_class=WorkloadClass.INTERACTIVE_CHAT,
                  prompt_segments=[(2, 1000)], output_len=3, deployment="chat-8b")
    _, summ = run(s, requests=[req])
    pm = PerfModel(get_model("llama-3.1-8b"), get_gpu("H100-SXM-80GB"), 1, s.hardware.perf)
    costs = [pm.step_time([(1000, 0)]), pm.step_time([(1, 1000)]), pm.step_time([(1, 1001)])]
    g = summ["gpu"]
    assert g["utilization_mean"] == pytest.approx(sum(max(c.compute_s, c.memory_s) for c in costs) / 10)
    assert g["achieved_flops_fraction_mean"] == pytest.approx(sum(c.compute_s * 0.5 for c in costs) / 10)
    assert g["achieved_hbm_bandwidth_fraction_mean"] == pytest.approx(sum(c.memory_s * 0.8 for c in costs) / 10)


def test_injected_requests_must_be_in_arrival_order():
    mk = lambda rid, t: Request(rid=rid, arrival=t, workload_class=WorkloadClass.BATCH,
                                prompt_segments=[(3 * rid, 10)], output_len=2, deployment="chat-8b")
    with pytest.raises(ValueError, match="arrival order"):
        run(scenario(), requests=[mk(1, 1.0), mk(2, 0.5)])
