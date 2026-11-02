# Capacity report

## Evidence status

This document separates evidence tiers deliberately:

- **SIMULATED** results below come from `inferia-sim`; they are not GPU
  measurements.
- **MEASURED (platform)** results exercise the local Kubernetes data plane,
  but do not make engine-performance claims when their backend is the CPU
  simulator.
- **MEASURED** calibration and real-engine results are pending a GPU lease.
- **EXTRAPOLATED** capacity claims are prohibited until the calibration error
  is recorded.

## Method

Each scenario is a seeded discrete-event simulation with a roofline timing
model, continuous batching, paged KV allocation and the workload described in
its YAML. Latency percentiles include requests arriving after the scenario
warm-up. The full simulator contract and assumptions are in
[`src/simulator/README.md`](../../../src/simulator/README.md).

## Reproducible baseline: 8×H100

Command run on 2026-11-02:

```bash
PYTHONPATH=src /home/pranav/.venvs/inferia-py311/bin/python \
  -m simulator.cli run 8xH100 --seed 1 \
  --out /tmp/inferia-capacity-8h100 --write-requests
```

| Metric | Result | Evidence |
|---|---:|---|
| Requests | 11,973 completed; 0 failed/shed/unfinished | SIMULATED |
| Request throughput | 40.3 requests/s | SIMULATED |
| Output throughput | 13,979.6 tokens/s | SIMULATED |
| TTFT p50 / p95 / p99 | 0.039 / 0.191 / 0.461 s | SIMULATED |
| ITL p50 / p95 | 0.0117 / 0.0196 s | SIMULATED |
| E2E p50 / p95 | 3.70 / 13.88 s | SIMULATED |
| KV prefix hit ratio | 0.279 (token basis) | SIMULATED |
| KV utilization mean / p95 | 0.117 / 0.210 | SIMULATED |
| GPU utilization mean | 0.791 | SIMULATED |
| TTFT p95 SLO (1 s) | met | SIMULATED |

The timing model had `calibration: uncalibrated` (`mfu=0.5`, HBM efficiency
`0.8`, 3 ms fixed step overhead), so this baseline only exercises simulator
behaviour and must not be used to size hardware.

## Reproducible baseline: 64×H100

```bash
PYTHONPATH=src /home/pranav/.venvs/inferia-py311/bin/python \
  -m simulator.cli run 64xH100 --seed 1 \
  --out /tmp/inferia-capacity-64h100 --write-requests
```

| Metric | Result | Evidence |
|---|---:|---|
| Requests | 18,091 completed; 0 failed/shed/unfinished | SIMULATED |
| Request / output throughput | 60.63 requests/s / 22,491.2 tokens/s | SIMULATED |
| TTFT p50 / p95 / p99 | 0.115 / 0.657 / 1.418 s | SIMULATED |
| ITL p50 / p95 | 0.0217 / 0.1110 s | SIMULATED |
| KV prefix hit ratio | 0.215 (token basis) | SIMULATED |
| KV utilization mean / p95 | 0.238 / 0.392 | SIMULATED |
| GPU utilization mean | 0.813 | SIMULATED |
| TTFT p95 SLO (2 s) | met | SIMULATED |

This is a distinct 70B TP=4 workload and is not comparable to the 8B baseline
as a scaling-efficiency claim. It only confirms deterministic execution of the
configured multi-node scenario before calibration.

## Calibration gate

The GPU lease harness is ready at
[`bench/lease/run_lease.sh`](../../../bench/lease/run_lease.sh). Before this
report can make capacity recommendations, it must record real vLLM runs for
the matching model, prompt/output matrix and rate ramps, then compare TTFT,
ITL and throughput with simulator replays. The target is sub-15% MAPE below
saturation; a higher error will be reported rather than hidden.

## Reproducible baseline: 256×H100

Command run on 2026-11-02:

```bash
PYTHONPATH=src /home/pranav/.venvs/inferia-py311/bin/python \
  -m simulator.cli run 256xH100 --seed 1 \
  --out /tmp/inferia-capacity-256h100 --write-requests
```

| Metric | Result | Evidence |
|---|---:|---|
| Requests | 89,951 completed; 0 failed/shed/unfinished | SIMULATED |
| Request / output throughput | 273.33 requests/s / 106,789.8 tokens/s | SIMULATED |
| TTFT p50 / p95 / p99 | 0.397 / 2.888 / 138.051 s | SIMULATED |
| ITL p50 / p95 | 0.0333 / 0.6993 s | SIMULATED |
| E2E p50 / p95 | 25.43 / 132.95 s | SIMULATED |
| KV prefix hit ratio | 0.220 (token basis) | SIMULATED |
| KV utilization mean / p95 | 0.704 / 0.999 | SIMULATED |
| Preemptions | 6,893 | SIMULATED |
| TTFT p95 SLO (2 s) | missed | SIMULATED |

This intentionally pressures the placeholder model beyond its knee, exposing
the failure mode rather than presenting an optimistic capacity claim. Its
timing model is uncalibrated and it must not be used to size hardware.

## Remaining CPU runs

- Generate standard plots from the resulting request/timeline artifacts.
- Model S1–S3 user populations only after calibrated per-cell measurements
  establish a defensible scaling basis.

## KV saturation policy sweep

The CPU sweep is reproducible with:

```bash
PYTHONPATH=src python bench/simulator/run_kv_sweep.py \
  --out bench/results/sim-kv-sweep
```

Its default pressure workload completed 611 requests in each policy but showed
the expected preemption/latency trade-off: recompute had TTFT p95 203.22 s;
legacy CPU swap 219.15 s; explicit CPU tier 235.63 s; and deliberately slow
NVMe 351.05 s. The swap variants moved 32,915 KV blocks while recompute moved
none. These are **SIMULATED, uncalibrated** stress results—not evidence that
offload is slower on real hardware. They validate that tier bandwidth and
preemption policy change the modelled saturation outcome, which the GPU lease
must calibrate.

## P/D transfer policy sweep

The CPU-only comparison is reproducible with:

```bash
PYTHONPATH=src python bench/simulator/run_pd_sweep.py \
  --out bench/results/sim-pd-sweep
python bench/simulator/render_kv_sweep.py \
  bench/results/sim-pd-sweep/comparison.json \
  --metric e2e_p95_s --out bench/results/sim-pd-sweep/e2e-p95.svg
```

It compares two monolithic replicas with a 1-prefill/1-decode split under a
fast (400 Gb/s, 0.5 ms) and deliberately slow (8 Gb/s, 10 ms) KV-transfer
link. All outputs are **SIMULATED, uncalibrated**. The sweep is a policy and
failure-mode exercise; it does not claim that either topology is faster until
the GPU lease measures the matching model and interconnect.

The 2026-11-02 run completed all 187 requests per variant with no failures.
Its E2E p95 was 6.14 s monolithic, 4.76 s with the placeholder fast link, and
5.31 s with the deliberately slow link. The TTFT trade-off was 0.254 s versus
1.119 s for both P/D variants; the first response comes from the prefill pool,
while transfer time is reflected after it. These modelled trade-offs are a
reason to calibrate—not an engine-topology recommendation.

## Local llm-d prefix-routing smoke

On 2026-11-02, a fresh local kind cluster ran the standard 12-session ×
6-turn routing demo through both entry points (72 requests each). All 144
requests returned HTTP 200. This is **MEASURED (platform), engine timing
simulated**: it validates endpoint wiring, KV-event-driven affinity and
metrics collection, not GPU latency or throughput.

| Entry point | Stickiness | Mean pods/session | Token prefix-hit ratio | Client p50 / p95 |
|---|---:|---:|---:|---:|
| llm-d EPP precise-prefix | 1.000 | 1.00 | 0.809 | 32.9 / 338.4 ms |
| kube-proxy baseline | 0.333 | 3.33 | 0.431 | 315.5 / 329.4 ms |

The EPP result is near the workload's ~0.81 theoretical cache-hit ceiling.
The apparent latency difference is produced by the simulator's uncached-token
cost and must not be interpreted as a real-engine performance result.
