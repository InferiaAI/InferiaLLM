# Capacity report

## Evidence boundary

This report records reproducible **SIMULATED** and **MEASURED (platform),
engine timing simulated** evidence only. It contains no real-GPU throughput,
latency, saturation, or capacity recommendation. Those remain pending GPU
calibration.

## CPU-verifiable baselines

The committed simulator provides seeded scenario baselines for `8xH100`,
`64xH100`, and `256xH100`. Run one with:

```sh
inferia-sim validate 8xH100
inferia-sim run 8xH100 --seed 1 --out bench/results/8xH100-seed1 --write-requests
```

The output embeds its scenario/configuration manifest. Repeating a scenario
with the same seed is deterministic. These baselines exercise continuous
batching, paged KV allocation, prefix caching, admission and the configured
topology; their roofline efficiencies and overheads are uncalibrated
placeholders.

Additional committed CPU-only policy exercises cover KV eviction/offload,
autoscaling activation and prefill/decode transfer. Their results are useful
for checking model invariants and comparing configured policies, not for
choosing hardware or topology.

## Local routing evidence

The kind llm-d demonstration is run separately:

```sh
./deploy/kind/up.sh
python3 deploy/kind/demo/prefix_routing.py --out /tmp/inferia-kind-prefix-routing
./deploy/kind/down.sh
```

It validates real Kubernetes/Envoy/EPP wiring, KV-event-driven endpoint
affinity and metrics collection with `llm-d-inference-sim`. Label outputs
**MEASURED (platform), engine timing simulated**. They must never be reported
as vLLM GPU latency or throughput.

A separate committed bounded-load smoke exercised 200 sessions × 3 turns (600
requests total) with 50 sessions in flight and zero errors. It retained 1.000
llm-d stickiness. This is also **MEASURED (platform), engine timing
simulated**—not a throughput benchmark and not evidence for the unverified
10k-concurrency exercise.

## Calibration gate

Before capacity extrapolation, run the lease harness in dry-run mode, then on
a confirmed GPU host only:

```sh
bash bench/lease/run_lease.sh --dry-run --base-url http://127.0.0.1:8000
# GPU host only, after review:
bash bench/lease/run_lease.sh --execute --base-url "$GPU_ENDPOINT"
```

Keep `manifest.env`, benchmark logs and raw results. Replay matching simulator
scenarios and record error for TTFT, inter-token latency and throughput. Until
that error is published, the simulator is **SIMULATED, uncalibrated** and no
EXTRAPOLATED sizing claim is permitted.
