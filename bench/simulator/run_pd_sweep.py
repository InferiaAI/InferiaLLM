#!/usr/bin/env python3
"""Run a reproducible, explicitly SIMULATED prefill/decode transfer sweep."""

import argparse
import json
from pathlib import Path

import yaml

from simulator.config import parse_scenario, read_scenario_bytes
from simulator.engine import Simulation
from simulator.report import summarize


def scenario(variant: str):
    data = yaml.safe_load(read_scenario_bytes("tiny"))
    data["cluster"]["pools"][0]["nodes"] = 2
    dep = data["deployment"][0]
    dep.update({"replicas": 2, "max_model_len": 8192})
    data["workload"]["arrival"]["rate_rps"] = 6
    data["workload"]["classes"][0]["input_tokens"] = {"dist": "fixed", "value": 4000}
    data["workload"]["classes"][0]["output_tokens"] = {"dist": "fixed", "value": 256}
    data["run"].update({"duration_s": 30, "warmup_s": 5})
    if variant == "monolithic":
        pass
    elif variant == "pd_fast":
        dep["disaggregation"] = {
            "prefill_replicas": 1,
            "decode_replicas": 1,
            "transfer_bandwidth_gbps": 400,
            "transfer_latency_s": 0.0005,
        }
    elif variant == "pd_slow":
        dep["disaggregation"] = {
            "prefill_replicas": 1,
            "decode_replicas": 1,
            "transfer_bandwidth_gbps": 8,
            "transfer_latency_s": 0.01,
        }
    else:
        raise ValueError(variant)
    data["name"] = f"pd-sweep-{variant}"
    return parse_scenario(yaml.safe_dump(data).encode())


def main() -> int:
    parser = argparse.ArgumentParser(description="SIMULATED P/D transfer sweep")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for variant in ("monolithic", "pd_fast", "pd_slow"):
        configured = scenario(variant)
        result = Simulation(configured).run()
        summary = summarize(result, configured, config_hash="pd-sweep", command="bench/simulator/run_pd_sweep.py")
        (args.out / f"{variant}.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        rows.append({
            "variant": variant,
            "label": "SIMULATED",
            "ttft_p95_s": summary["latency"]["ttft_seconds"]["p95"],
            "itl_p95_s": summary["latency"]["itl_seconds"]["p95"],
            "e2e_p95_s": summary["latency"]["e2e_seconds"]["p95"],
            "completed": summary["totals"]["completed"],
            "failed": summary["totals"]["failed"],
        })
    comparison = {"label": "SIMULATED", "variants": rows}
    (args.out / "comparison.json").write_text(json.dumps(comparison, indent=2) + "\n")
    print(json.dumps(comparison, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
