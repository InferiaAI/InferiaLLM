# Simulator capacity baseline — CPU-backed execution

**Status: SIMULATED / uncalibrated.** This document records a repeatable
CPU-backed execution of the discrete-event model. It contains no GPU
measurements and must not be used as a hardware capacity commitment.

## Baseline

- Scenario: `tiny`; seed: `1`; invariant checks: enabled.
- Model configuration: H100 roofline catalogue entry with placeholder
  efficiencies (`mfu=0.5`, `hbm_efficiency=0.8`) and calibration label
  `uncalibrated`.
- CPU execution is only the simulator host; it does **not** benchmark an H100.

| Metric | Simulated result |
| --- | ---: |
| Requests completed | 125 |
| Requests/s | 2.12 |
| Output tokens/s | 511.28 |
| TTFT p95 | 0.0514 s |
| Prefix-cache token hit ratio | 31.32% |
| KV utilisation mean / p95 | 0.96% / 1.70% |

## Repeat

```bash
PYTHONPATH=src python -c 'from simulator.config import load_scenario; from simulator.engine import Simulation; s,_=load_scenario("tiny"); print(Simulation(s, seed=1, check_invariants=True).run())'
```

## GPU calibration placeholders (deferred)

Before treating any simulated throughput, latency, or capacity as an estimate,
run controlled GPU leases and replace the placeholder MFU, HBM efficiency,
fixed step overhead, and collective latency. Record GPU SKU, driver, CUDA,
engine revision, model revision, workload trace, and confidence intervals in
the resulting manifest. Calibration is explicitly deferred; no calibration
has begun in this session.
