# 0003 — Build our own discrete-event simulator, calibrated on real GPUs

## Context
The brief requires simulating 10M–100M users, bursty traffic, many GPU and cluster configurations, KV saturation, network and model-loading costs, and failures, with realistic resource constraints. We only have short on-demand GPU leases.

## Decision
Build `inferia-sim` (`src/simulator/`, Session 2), a Python discrete-event simulator with:
- **one event per engine iteration**, not per token
- a **roofline GPU model** whose efficiencies are fitted from lease measurements
- a **paged KV block manager** with prefix cache, LRU eviction, and recompute/swap preemption
- continuous batching with chunked prefill
- routers (including KV-aware), an autoscaler with cold starts, failures, prefill/decode disaggregation, and an offload tier
- a workload generator built from user populations

It is calibrated against real vLLM runs (MAPE reported) and cross-checked with Dynamo's AI Configurator or Vidur where usable.

## Alternatives considered
- **Vidur only:** high per-replica fidelity (under 9% reported error), but no user-population, multi-cluster, autoscaling or failure modelling for our scenarios.
- **Dynamo AIC only:** configuration search, not traffic dynamics over time.
- **inference-sim or Dynamo mocker only:** live HTTP stand-ins for real-software tests, not capacity models.
- **Analytical spreadsheet only:** can't show queueing, bursts, saturation or failure dynamics.

## Consequences
- We own the model's correctness. That's mitigated by invariant tests, property-based KV tests, analytical sanity tests, golden scenarios and published calibration error.
- Uncalibrated GPUs are reported as "roofline-scaled, uncalibrated".

## Status
Accepted (2026-10-31).

## Sources
- Vidur: https://www.microsoft.com/en-us/research/publication/vidur-a-large-scale-simulation-framework-for-llm-inference/
- Dynamo profiler / AIC: https://docs.nvidia.com/dynamo/latest/components/profiler/profiler-guide.md
- `plan.md` WS3
