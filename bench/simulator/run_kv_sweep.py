#!/usr/bin/env python3
"""Run a reproducible SIMULATED KV-policy comparison on CPU."""

import argparse
import json
from pathlib import Path

import yaml

from simulator.config import parse_scenario, read_scenario_bytes
from simulator.engine import Simulation
from simulator.report import summarize


def scenario(variant: str):
    data = yaml.safe_load(read_scenario_bytes("tiny"))
    dep = data["deployment"][0]
    dep.update({"gpu_memory_utilization": 0.24, "max_model_len": 2048})
    data["workload"]["arrival"]["rate_rps"] = 10
    data["workload"]["classes"][0]["input_tokens"] = {"dist": "fixed", "value": 600}
    data["workload"]["classes"][0]["output_tokens"] = {"dist": "fixed", "value": 400}
    data["run"].update({"duration_s": 60, "warmup_s": 10})
    if variant == "recompute":
        dep["preemption"] = "recompute"
    elif variant == "swap_cpu":
        dep.update({"preemption": "swap", "swap_space_gb": 4})
    elif variant == "offload_cpu":
        dep.update({"preemption": "swap", "offload": {"tier": "cpu", "capacity_gb": 4, "bandwidth_gbps": 64}})
    elif variant == "offload_nvme":
        dep.update({"preemption": "swap", "offload": {"tier": "nvme", "capacity_gb": 16, "bandwidth_gbps": 8}})
    else:
        raise ValueError(variant)
    data["name"] = f"kv-sweep-{variant}"
    return parse_scenario(yaml.safe_dump(data).encode())


def main() -> int:
    parser = argparse.ArgumentParser(description="SIMULATED KV policy sweep")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for variant in ("recompute", "swap_cpu", "offload_cpu", "offload_nvme"):
        s = scenario(variant)
        result = Simulation(s).run()
        summary = summarize(result, s, config_hash="kv-sweep", command="bench/simulator/run_kv_sweep.py")
        with (args.out / f"{variant}.json").open("w") as f:
            json.dump(summary, f, indent=2, sort_keys=True)
            f.write("\n")
        rows.append({
            "variant": variant,
            "label": "SIMULATED",
            "ttft_p95_s": summary["latency"]["ttft_seconds"]["p95"],
            "preemptions": summary["totals"]["preempted"],
            "swapped_out_blocks": summary["kv"]["swapped_out_blocks"],
            "completed": summary["totals"]["completed"],
        })
    with (args.out / "comparison.json").open("w") as f:
        json.dump({"label": "SIMULATED", "variants": rows}, f, indent=2)
        f.write("\n")
    print(json.dumps({"label": "SIMULATED", "variants": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
