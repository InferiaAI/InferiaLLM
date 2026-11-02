# Capacity report

## Evidence status

This document separates evidence tiers deliberately:

- **SIMULATED** results below come from `inferia-sim`; they are not GPU
  measurements.
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

## Remaining CPU runs

- Run and archive the 256× scenario baseline.
- Run policy sweeps for saturation, prefix routing, offload and P/D transfer.
- Generate standard plots from the resulting request/timeline artifacts.
- Model S1–S3 user populations only after calibrated per-cell measurements
  establish a defensible scaling basis.
