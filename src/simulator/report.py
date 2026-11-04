"""``summary.json`` (docs/platform/interfaces.md §9) and the run manifest (§1).

Latency percentiles cover requests that ARRIVE inside the measurement
window [warmup_s, duration_s]; throughput counts completions and tokens
emitted inside it. Prefix-cache hit ratio is token-based, like vLLM's
``vllm:prefix_cache_hits`` / ``vllm:prefix_cache_queries``.

``created_at`` honours ``SOURCE_DATE_EPOCH`` (the reproducible-builds
convention) so a rerun with the same seed can be byte-identical.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from simulator import __version__
from simulator.config import Scenario
from simulator.engine import SimResult
from simulator.hardware import get_model
from simulator.metrics import percentile
from simulator.workload import describe

_PCTS = {"p50": 50, "p95": 95, "p99": 99}


def _created_at() -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    when = (
        datetime.fromtimestamp(int(epoch), tz=timezone.utc)
        if epoch and epoch.isdigit()
        else datetime.now(timezone.utc)
    )
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _git() -> tuple[str, Optional[bool]]:
    """(sha, dirty) of the checkout this package runs from; ("unknown", None) when installed."""
    sha = os.environ.get("INFERIA_GIT_SHA")
    if sha:
        return sha, None
    here = str(Path(__file__).resolve().parent)
    try:
        sha = subprocess.run(
            ["git", "-C", here, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", here, "status", "--porcelain"], capture_output=True, text=True, timeout=5, check=True
        ).stdout.strip()
        return sha, bool(dirty)
    except (OSError, subprocess.SubprocessError):
        return "unknown", None


def _dist(values: list[float]) -> dict[str, Optional[float]]:
    return {k: percentile(values, p) for k, p in _PCTS.items()}


def build_manifest(result: SimResult, scenario: Scenario, config_hash: str, command: str) -> dict:
    sha, dirty = _git()
    gpus: dict[str, int] = {}
    for r in result.replicas:
        gpus[r.gpu.name] = gpus.get(r.gpu.name, 0) + r.dep.tensor_parallel
    models = [get_model(m) for m in scenario.models]
    perf = scenario.hardware.perf
    mixes = scenario.workload.classes
    return {
        "schema_version": "1",
        "run_id": f"{scenario.name}-seed{result.seed}",
        "label": "SIMULATED",
        "label_detail": (
            "inferia-sim discrete-event simulation; roofline performance model "
            f"(calibration: {perf.calibration})"
        ),
        "created_at": _created_at(),
        "git_sha": sha,
        "git_dirty": dirty,
        "command": command,
        "tool": {"name": "inferia-sim", "version": __version__},
        "config_hash": config_hash,
        "seed": result.seed,
        "hardware": {
            "provider": "simulated",
            "gpu_model": ",".join(sorted(gpus)),
            "gpu_count": sum(gpus.values()),
            "gpus_by_model": dict(sorted(gpus.items())),
            "driver": None,
            "cuda": None,
            "cpu": None,
            "ram_gb": None,
        },
        "engine": {
            "name": "inferia-sim vLLM-V1-like scheduler model",
            "version": __version__,
            "image": None,
            "flags": [],
        },
        "model": {
            "id": ",".join(m.hf_id for m in models),
            "revision": None,
            "dtype": ",".join(sorted({m.dtype for m in models})),
            "quantization": None,
        },
        "workload": {
            "description": scenario.description,
            "dataset": (
                f"{scenario.workload.arrival.trace_dataset} ({scenario.workload.arrival.trace_license})"
                if scenario.workload.arrival.process == "trace"
                else "synthetic"
            ),
            "arrival": f"{scenario.workload.arrival.process} rate={scenario.workload.effective_rate_rps()}",
            "input_tokens": "; ".join(f"{m.workload_class.value}: {describe(m.input_tokens)}" for m in mixes),
            "output_tokens": "; ".join(f"{m.workload_class.value}: {describe(m.output_tokens)}" for m in mixes),
        },
        "derived_from": [],
        "notes": "Simulated numbers are not GPU measurements.",
    }


def summarize(result: SimResult, scenario: Scenario, config_hash: str, command: str) -> dict:
    w0, w1 = result.window
    span = w1 - w0
    recs = result.records
    counts = {k: 0 for k in ("completed", "failed", "shed", "cancelled", "unfinished")}
    reasons: dict[str, int] = {}
    for r in recs:
        counts[r.status] += 1
        if r.status == "failed":
            reasons[r.reason] = reasons.get(r.reason, 0) + 1
    in_window = [r for r in recs if w0 <= r.arrival < w1]
    done = [r for r in in_window if r.status == "completed"]
    completed_in_window = sum(1 for r in recs if r.status == "completed" and w0 <= r.finish <= w1)
    out_tokens = sum(s.output_tokens for s in result.stats)

    replicas, stats = result.replicas, result.stats
    gpu_weight = [r.dep.tensor_parallel for r in replicas]
    total_gpus = sum(gpu_weight)

    def weighted(values: list[float]) -> float:
        return sum(v * w for v, w in zip(values, gpu_weight)) / total_gpus

    kv_means = [s.kv_util.mean() for s in stats]
    kv_p95 = [s.kv_util.quantile(0.95) for s in stats]
    query = sum(r.query_tokens for r in replicas)
    hits = sum(r.hit_tokens for r in replicas)

    nodes: dict[str, dict] = {}
    for r, s in zip(replicas, stats):
        n = nodes.setdefault(r.node, {"node": r.node, "gpu_model": r.gpu.name, "replicas": 0,
                                      "_tokens": 0, "_kv": []})
        n["replicas"] += 1
        n["_tokens"] += s.output_tokens
        n["_kv"].append(s.kv_util.mean())
    per_node = [
        {
            "node": n["node"],
            "gpu_model": n["gpu_model"],
            "replicas": n["replicas"],
            "output_tokens_per_second": n["_tokens"] / span,
            "kv_utilization_mean": sum(n["_kv"]) / len(n["_kv"]),
        }
        for n in nodes.values()
    ]

    ttft = [r.ttft for r in done]
    latency = {
        "ttft_seconds": _dist(ttft),
        "itl_seconds": {k: result.itl.quantile(p / 100) for k, p in _PCTS.items()},
        "e2e_seconds": _dist([r.e2e for r in done]),
        "queue_seconds": _dist([r.queue for r in done]),
    }
    target = scenario.run.slo_ttft_p95_s
    p95 = latency["ttft_seconds"]["p95"]

    admitted = [r for r in recs if r.status in ("completed", "unfinished")]
    finished = [r for r in admitted if r.status == "completed"]
    win_adm = [r for r in admitted if w0 <= r.arrival < w1]
    win_fin = [r for r in win_adm if r.status == "completed"]
    end = result.end_time

    return {
        "manifest": build_manifest(result, scenario, config_hash, command),
        "scenario": {"name": scenario.name, "config_hash": config_hash},
        "window": {"start_s": w0, "end_s": w1, "run_end_s": end},
        "totals": {
            "requests": len(recs),
            **counts,
            "preempted": sum(r.preemptions for r in replicas),
        },
        "failure_reasons": dict(sorted(reasons.items())),
        "throughput": {
            "requests_per_second": completed_in_window / span,
            "output_tokens_per_second": out_tokens / span,
        },
        "latency": latency,
        "kv": {
            "utilization_mean": weighted(kv_means),
            "utilization_p95": max(kv_p95),
            "prefix_cache_hit_ratio": hits / query if query else 0.0,
            "prefix_cache_basis": "tokens",
            "evictions": sum(r.kv.evictions for r in replicas),
            "offload_reloaded_blocks": sum(getattr(r.kv, "offload_hits", 0) for r in replicas),
            "swapped_out_blocks": sum(r.kv.swapped_out_blocks for r in replicas),
            "internal_waste_ratio_mean": weighted([s.waste_ratio.mean() for s in stats]),
            "external_fragmentation_mean": weighted([s.ext_frag.mean() for s in stats]),
        },
        "gpu": {
            # interfaces §2: compute-or-memory-bound time / wall time.
            "utilization_mean": weighted([s.bound_s / span for s in stats]),
            "achieved_flops_fraction_mean": weighted([s.flops_s / span for s in stats]),
            "achieved_hbm_bandwidth_fraction_mean": weighted([s.hbm_s / span for s in stats]),
        },
        "per_node": per_node,
        "slo": {"ttft_p95_target_s": target, "met": (p95 is not None and p95 <= target) if target else None},
        "admission": {
            "max_waiting_per_replica": scenario.admission.max_waiting_per_replica,
            "retry_after_s": scenario.admission.retry_after_s,
        },
        "autoscaler": {
            "enabled": scenario.autoscaler.enabled,
            "scale_ups": result.autoscaler_scaleups,
            "metric": scenario.autoscaler.metric,
            "target": scenario.autoscaler.target,
        },
        "failures": {"enabled": scenario.failures.enabled, "retries": result.failure_retries},
        "littles_law": {
            "full_horizon": {
                "L": result.in_system_integral_full / end,
                "lambda": len(admitted) / end,
                "W": sum(r.e2e for r in finished) / len(finished) if finished else None,
            },
            "window": {
                "L": result.in_system_integral_window / span,
                "lambda": len(win_adm) / span,
                "W": sum(r.e2e for r in win_fin) / len(win_fin) if win_fin else None,
            },
        },
        "calibration": scenario.hardware.perf.model_dump(),
        "artifacts": {"requests": None, "timeline": None, "plots": []},
    }


_REQUEST_FIELDS = [
    "rid", "workload_class", "deployment", "replica", "status", "reason", "arrival",
    "prompt_len", "output_len", "generated", "preemptions", "queue", "ttft", "e2e",
]


def _simulated_svg(title: str, values: list[float]) -> str:
    """Small dependency-free SVG artifact; intentionally watermarked."""
    width, height = 640, 240
    peak = max(values, default=1.0) or 1.0
    points = " ".join(
        f"{40 + i * 560 / max(1, len(values) - 1):.1f},{200 - value / peak * 150:.1f}"
        for i, value in enumerate(values)
    )
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
            f'<rect width="100%" height="100%" fill="white"/><text x="24" y="28">{title}</text>'
            f'<polyline fill="none" stroke="#2563eb" stroke-width="2" points="{points}"/>'
            '<text x="430" y="220" fill="#94a3b8" font-size="24">SIMULATED</text></svg>')


def write_outputs(summary: dict, result: SimResult, out_dir: Path, write_requests: bool, write_plots: bool = False) -> None:
    """Write summary.json, manifest.json and, optionally, requests.csv."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if write_requests:
        with open(out_dir / "requests.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(_REQUEST_FIELDS)
            for r in result.records:
                row = [getattr(r, k) for k in _REQUEST_FIELDS]
                row[1] = r.workload_class.value
                w.writerow(row)
        summary["artifacts"]["requests"] = "requests.csv"
    if write_plots:
        plots = {
            "ttft.svg": [r.ttft or 0.0 for r in result.records if r.status == "completed"],
            "e2e.svg": [r.e2e or 0.0 for r in result.records if r.status == "completed"],
        }
        for name, values in plots.items():
            (out_dir / name).write_text(_simulated_svg(name[:-4].upper(), values), encoding="utf-8")
        summary["artifacts"]["plots"] = list(plots)
    for name, obj in (("summary.json", summary), ("manifest.json", summary["manifest"])):
        with open(out_dir / name, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, sort_keys=True)
            f.write("\n")


def command_line() -> str:
    return " ".join(["inferia-sim", *sys.argv[1:]])
