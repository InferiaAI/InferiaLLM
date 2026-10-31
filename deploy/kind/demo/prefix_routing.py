#!/usr/bin/env python3
"""Prefix-aware routing demo: llm-d EPP vs a cache-unaware baseline, on the kind spike.

Sends the same multi-turn, shared-prefix workload through two entry points that front
the *same* llm-d-inference-sim pods:

  * ``llmd``      http://127.0.0.1:18911  Envoy sidecar -> llm-d EPP (precise prefix-cache
                  routing from the sims' KV events) -> sim pod
  * ``baseline``  http://127.0.0.1:18912  plain NodePort Service -> kube-proxy (random) -> sim pod

and reports, per entry point:

  * routing ground truth from the sim's ``x-inference-pod`` response header:
    session stickiness (a turn lands on the pod that served the session's previous turn)
    and distinct pods per session
  * the sims' own prefix-cache counters (``vllm:prefix_cache_hits_total`` /
    ``vllm:prefix_cache_queries_total``, token-based), diffed over the run
  * client-observed latency (engine timing is simulated by the sims)

Evidence label: MEASURED (platform), engine timing simulated (interfaces.md section 1).

Caches are reset between entry points by restarting the sim Deployment (``--reset``,
default on), so neither run benefits from the other's cached blocks.

Standard library only; needs ``kubectl`` for the reset and the metrics scrape.

    python3 deploy/kind/demo/prefix_routing.py --out /tmp/run1
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import random
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

ENDPOINTS = {
    "llmd": "http://127.0.0.1:18911",
    "baseline": "http://127.0.0.1:18912",
}
METRIC_HITS = "vllm:prefix_cache_hits_total"
METRIC_QUERIES = "vllm:prefix_cache_queries_total"


@dataclass
class Result:
    endpoint: str
    session: int
    turn: int
    pod: str | None
    status: int
    latency_s: float
    error: str | None = None


@dataclass
class Summary:
    endpoint: str
    requests: int
    errors: int
    stickiness: float | None  # turns >= 2 served by the previous turn's pod
    mean_pods_per_session: float
    pod_request_counts: dict[str, int] = field(default_factory=dict)
    prefix_cache_hits_tokens: float = 0.0
    prefix_cache_queries_tokens: float = 0.0
    prefix_cache_hit_ratio: float | None = None
    latency_p50_s: float | None = None
    latency_p95_s: float | None = None


def kubectl(args: list[str], context: str, namespace: str) -> str:
    """Run kubectl with fixed argv (no shell) against the spike cluster."""
    out = subprocess.run(
        ["kubectl", "--context", context, "-n", namespace, *args],
        check=True, capture_output=True, text=True, timeout=300,
    )
    return out.stdout


def sim_pods(context: str, namespace: str) -> list[str]:
    names = kubectl(
        ["get", "pods", "-l", "llm-d.ai/guide=inferia-spike",
         "--field-selector=status.phase=Running", "-o", "jsonpath={.items[*].metadata.name}"],
        context, namespace,
    ).split()
    return sorted(names)


def scrape_prefix_counters(context: str, namespace: str) -> dict[str, tuple[float, float]]:
    """Return {pod: (hits_tokens, queries_tokens)} from each sim pod's /metrics."""
    counters: dict[str, tuple[float, float]] = {}
    for pod in sim_pods(context, namespace):
        text = subprocess.run(
            ["kubectl", "--context", context, "get", "--raw",
             f"/api/v1/namespaces/{namespace}/pods/{pod}:8000/proxy/metrics"],
            check=True, capture_output=True, text=True, timeout=60,
        ).stdout
        hits = queries = 0.0
        for line in text.splitlines():
            if line.startswith("#"):
                continue
            name = line.split("{", 1)[0].split(" ", 1)[0]
            if name == METRIC_HITS:
                hits += float(line.rsplit(" ", 1)[1])
            elif name == METRIC_QUERIES:
                queries += float(line.rsplit(" ", 1)[1])
        counters[pod] = (hits, queries)
    return counters


def reset_sims(context: str, namespace: str) -> None:
    """Restart the sims so every run starts with empty KV caches."""
    kubectl(["rollout", "restart", "deployment/sim"], context, namespace)
    kubectl(["rollout", "status", "deployment/sim", "--timeout=180s"], context, namespace)
    # Old pods must be gone so the metrics scrape only sees fresh caches.
    deadline = time.time() + 120
    while time.time() < deadline:
        phases = kubectl(["get", "pods", "-l", "llm-d.ai/guide=inferia-spike",
                          "-o", "jsonpath={.items[*].status.phase}"], context, namespace).split()
        if phases and all(p == "Running" for p in phases) and len(phases) == len(sim_pods(context, namespace)):
            break
        time.sleep(2)


def make_prefix(session: int, words: int, rng: random.Random) -> str:
    vocab = ["routing", "cache", "prefix", "token", "block", "scheduler", "replica", "gateway",
             "latency", "throughput", "session", "context", "decode", "prefill", "kernel", "memory"]
    body = " ".join(rng.choice(vocab) for _ in range(words))
    return f"System prompt for session {session}: {body}."


def send(base: str, prompt: str, max_tokens: int, timeout: float) -> tuple[int, str | None, float, str | None]:
    req = urllib.request.Request(
        f"{base}/v1/completions",
        data=json.dumps({"model": "sim-model", "prompt": prompt, "max_tokens": max_tokens}).encode(),
        headers={"Content-Type": "application/json", "Connection": "close"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            return resp.status, resp.headers.get("x-inference-pod"), time.perf_counter() - t0, None
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("x-inference-pod"), time.perf_counter() - t0, f"HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return 0, None, time.perf_counter() - t0, type(e).__name__


def run_endpoint(name: str, base: str, args: argparse.Namespace) -> list[Result]:
    rng = random.Random(args.seed)
    prefixes = [make_prefix(s, args.prefix_words, rng) for s in range(args.sessions)]

    def session_worker(s: int) -> list[Result]:
        out = []
        for t in range(args.turns):
            prompt = f"{prefixes[s]} Turn {t}: question {t} about llm-d?"
            status, pod, lat, err = send(base, prompt, args.max_tokens, args.timeout)
            out.append(Result(name, s, t, pod, status, lat, err))
            time.sleep(args.think_time)  # let KV events reach the EPP before the next turn
        return out

    results: list[Result] = []
    with cf.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for rs in pool.map(session_worker, range(args.sessions)):
            results.extend(rs)
    return results


def summarize(name: str, results: list[Result], before: dict, after: dict) -> Summary:
    by_session: dict[int, list[Result]] = {}
    for r in results:
        by_session.setdefault(r.session, []).append(r)
    stick = total = 0
    pods_per_session = []
    for rs in by_session.values():
        rs.sort(key=lambda r: r.turn)
        pods_per_session.append(len({r.pod for r in rs if r.pod}))
        for prev, cur in zip(rs, rs[1:]):
            if prev.pod and cur.pod:
                total += 1
                stick += prev.pod == cur.pod
    counts: dict[str, int] = {}
    for r in results:
        if r.pod:
            counts[r.pod] = counts.get(r.pod, 0) + 1
    hits = sum(after.get(p, (0, 0))[0] - before.get(p, (0, 0))[0] for p in after)
    queries = sum(after.get(p, (0, 0))[1] - before.get(p, (0, 0))[1] for p in after)
    lats = sorted(r.latency_s for r in results if r.status == 200)
    return Summary(
        endpoint=name,
        requests=len(results),
        errors=sum(1 for r in results if r.status != 200),
        stickiness=(stick / total) if total else None,
        mean_pods_per_session=statistics.fmean(pods_per_session) if pods_per_session else 0.0,
        pod_request_counts=dict(sorted(counts.items())),
        prefix_cache_hits_tokens=hits,
        prefix_cache_queries_tokens=queries,
        prefix_cache_hit_ratio=(hits / queries) if queries else None,
        latency_p50_s=statistics.median(lats) if lats else None,
        latency_p95_s=lats[int(0.95 * (len(lats) - 1))] if lats else None,
    )


def git_info() -> tuple[str, bool]:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip())
        return sha, dirty
    except (OSError, subprocess.CalledProcessError):
        return "unknown", True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True, help="run directory for results.json + manifest.json")
    p.add_argument("--endpoints", default="llmd,baseline", help="comma list from: " + ",".join(ENDPOINTS))
    p.add_argument("--sessions", type=int, default=12)
    p.add_argument("--turns", type=int, default=6)
    p.add_argument("--prefix-words", type=int, default=1500, help="shared prefix length per session (sim tokens ~ words)")
    p.add_argument("--max-tokens", type=int, default=8)
    p.add_argument("--concurrency", type=int, default=4, help="sessions in flight (turns stay sequential)")
    p.add_argument("--think-time", type=float, default=0.5, help="seconds between a session's turns")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--context", default="kind-inferia-s3")
    p.add_argument("--namespace", default="inferia-llmd")
    p.add_argument("--no-reset", dest="reset", action="store_false", help="don't restart sims between endpoints")
    args = p.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    names = [n.strip() for n in args.endpoints.split(",") if n.strip()]
    unknown = [n for n in names if n not in ENDPOINTS]
    if unknown:
        p.error(f"unknown endpoint(s): {unknown}")

    summaries, all_results = [], []
    for name in names:
        if args.reset:
            print(f"[{name}] resetting sim caches (rollout restart)...", flush=True)
            reset_sims(args.context, args.namespace)
            time.sleep(5)  # let the EPP discover the new pods and subscribe to their KV events
        before = scrape_prefix_counters(args.context, args.namespace)
        print(f"[{name}] {args.sessions} sessions x {args.turns} turns via {ENDPOINTS[name]}", flush=True)
        results = run_endpoint(name, ENDPOINTS[name], args)
        after = scrape_prefix_counters(args.context, args.namespace)
        s = summarize(name, results, before, after)
        summaries.append(s)
        all_results.extend(results)
        ratio = "n/a" if s.prefix_cache_hit_ratio is None else f"{s.prefix_cache_hit_ratio:.3f}"
        sticky = "n/a" if s.stickiness is None else f"{s.stickiness:.3f}"
        print(f"[{name}] stickiness={sticky} pods/session={s.mean_pods_per_session:.2f} "
              f"prefix_hit_ratio(token)={ratio} errors={s.errors} p50={s.latency_p50_s}", flush=True)

    sha, dirty = git_info()
    cmd = " ".join([Path(sys.argv[0]).name, *(argv if argv is not None else sys.argv[1:])])
    manifest = {
        "schema_version": "1",
        "run_id": out.name,
        "label": "MEASURED",
        "label_detail": "MEASURED (platform), engine timing simulated: llm-d-inference-sim v1.0.0 on kind, CPU only",
        "created_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": sha,
        "git_dirty": dirty,
        "command": cmd,
        "tool": {"name": "deploy/kind/demo/prefix_routing.py", "version": "1"},
        "config_hash": hashlib.sha256(json.dumps(vars(args), sort_keys=True).encode()).hexdigest(),
        "seed": args.seed,
        "hardware": {"provider": "local", "gpu_model": None, "gpu_count": 0, "driver": None, "cuda": None,
                     "cpu": os.uname().machine, "ram_gb": None},
        "engine": {"name": "llm-d-inference-sim", "version": "v1.0.0",
                   "image": "ghcr.io/llm-d/llm-d-inference-sim:v1.0.0@sha256:1908c421cbb44130a3212934a0dd1e279dacfe3480be93f4c28b45cd29b93ec8",
                   "flags": ["--enable-kvcache", "--block-size=64", "--kv-cache-size=2048"]},
        "model": {"id": "sim-model", "revision": None, "dtype": None, "quantization": None},
        "workload": {"description": f"{args.sessions} sessions x {args.turns} turns, unique ~{args.prefix_words}-word prefix per session",
                     "dataset": "synthetic (prefix_routing.py)", "arrival": f"closed loop, {args.concurrency} sessions in flight, think {args.think_time}s",
                     "input_tokens": f"~{args.prefix_words}+10 (sim tokenizer)", "output_tokens": f"{args.max_tokens}"},
        "derived_from": [],
        "notes": "Router: llm-d-router-standalone v1.0.0 (EPP v1.0.0, precise-prefix-cache-producer + prefix-cache-affinity-filter + token-load-scorer). Baseline: kube-proxy NodePort (random).",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out / "results.json").write_text(json.dumps(
        {"summaries": [asdict(s) for s in summaries], "requests": [asdict(r) for r in all_results]}, indent=2) + "\n")
    print(f"wrote {out}/manifest.json and results.json")
    return 0 if all(s.errors == 0 for s in summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
