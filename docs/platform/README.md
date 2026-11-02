# InferiaLLM platform evidence index

This index is the review entry point for the scale-and-enterprise work. Every
performance number is explicitly classified as **MEASURED**, **SIMULATED**, or
**EXTRAPOLATED**; simulation is never presented as hardware performance.

## Architecture and contracts

- [Platform specification](spec.md) — control/data-plane responsibilities,
  failure boundaries and framework choices.
- [Interfaces](interfaces.md) — workload classes, extension contracts,
  request metadata and output schema.
- [Architecture decisions](adr/README.md) — build-vs-adopt rationale.
- [System-context diagram](diagrams/01-system-context.svg) and
  [diagram source](diagrams/01-system-context.dot).

## Working implementation

- [Simulator guide](../../src/simulator/README.md) — deterministic DES,
  paging, routing, admission, offload, failure handling and P/D transfer.
- [kind llm-d demo](../../deploy/kind/README.md) — CPU-only precise-prefix
  routing demonstration. Its engine timing is simulated.
- [Helm chart](../../deploy/helm/inferiallm) — self-hosted deployment
  foundation.
- [Benchmark guide](../../bench/README.md) — CPU simulation, KV sweeps and
  GPU-lease rehearsal.

## Capacity and calibration

- [Capacity report](capacity/report.md) — reproducible 8× and 64× scenario
  baselines, all currently **SIMULATED** and uncalibrated.
- [GPU lease harness](../../bench/lease/run_lease.sh) — dry-run safe; only an
  explicit `--execute` on a GPU host produces **MEASURED** output.

## Current evidence boundary

The CPU-testable data-plane, simulator, packaging and Redis Streams usage
pipeline can be exercised locally. GPU calibration, real vLLM saturation,
multimodal engine smoke tests and calibrated capacity extrapolation remain
pending access to a suitable GPU lease. Those gaps are intentional evidence
boundaries, not simulated substitutes.
