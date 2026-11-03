# Reproducible simulated capacity matrix

This matrix runs the discrete-event simulator on a CPU host. It is **not a
GPU benchmark**. Every output is labelled `SIMULATED`; current roofline
efficiencies are `uncalibrated` placeholders pending deferred GPU calibration.

## Run

```bash
PYTHONPATH=src python scripts/run_simulator_capacity_matrix.py \
  --out bench/results/simulated-capacity-$(date +%F) --seed 1
```

The default matrix covers `tiny`, `8xH100`, `64xH100`,
`hetero-h100-a100-l40s`, and `256xH100`. Each scenario directory contains:

- `summary.json` and `manifest.json`, including seed, scenario hash, and the
  SIMULATED/uncalibrated label;
- `requests.csv` for workload-level inspection;
- `ttft.svg` and `e2e.svg`, both watermarked `SIMULATED`.

## Interpretation boundary

Use the matrix only to compare policies, workload mixes, KV/cache pressure,
and scheduling tradeoffs *inside the model*. Do not compare its output to
production GPU capacity until calibration replaces the placeholder MFU, HBM
efficiency, step overhead, and collective latency. GPU calibration remains
deferred.
