# Scale-and-enterprise implementation status

Evidence is current for branch `s3/dataplane`. “Verified” means a local test,
render, or executable command covered the item; it does not imply GPU
performance validation.

| Area | Status | Evidence / boundary |
|---|---|---|
| Architecture spec, ADRs, ten diagrams | Verified | `spec.md`, `adr/`, ten committed DOT/SVG pairs |
| llm-d kind prefix-routing spike | Verified CPU | 2026-11-02 fresh-cluster: 144/144 routing smoke HTTP 200; 0.809 EPP vs 0.431 baseline token cache-hit ratio; deleted sim pod replaced while EPP kept serving; engine timing remains simulated |
| llm-d routing metadata and stream cancellation | Verified CPU | inference routing/cancellation tests |
| Async usage pipeline | Verified CPU | Redis producer → batch worker → local Postgres → ACK verified; retry and empty-pending-frame regression tests; runbook documents at-least-once semantics |
| Simulator core, KV, routing, failure/retry | Verified CPU | simulator config/KV/perf/CLI suites and targeted engine tests |
| Offload, P/D and autoscale model | Verified CPU | explicit tier, transfer, signal/cooldown tests plus runnable P/D policy sweep; numbers uncalibrated |
| Capacity baselines and KV sweep | Verified CPU | 8×/64× reports plus runnable sweep/chart; **SIMULATED** only |
| Helm self-hosting foundation | Verified render | default and backup-enabled `helm template` renders |
| Backup/recovery documentation | Verified local | `pg_dump`/`pg_restore` completed into a fresh scratch DB; Helm CronJob PVC/credentials remain environment-dependent |
| Enterprise identity/RBAC/extension seams | Existing + documented | existing control-plane code and extension registry |
| Dashboard production build | Verified CPU | `npm run build` completed after lockfile install; existing bundle-size warning and dependency audit findings remain |
| Dashboard infrastructure/capacity views | Not verified complete | requires focused UI implementation/review |
| Full multimodal class/job workflow | Partial | endpoint foundations exist; GPU engine smoke tests pending |
| Real vLLM calibration | Blocked on GPU | lease runner rehearsed; must collect real H100/A10 evidence |
| Real KV saturation/offload and multimodal smoke | Blocked on GPU | must remain **MEASURED** only after lease execution |
| Bounded kind concurrency exercise | Verified CPU | 600 requests / 50 concurrent sessions: 0 errors, 1.000 llm-d stickiness; engine timing simulated. The 10k exercise remains unverified. |

## Next gates

1. Run the GPU lease harness against a healthy real vLLM endpoint and retain
   `manifest.env` plus benchmark output.
2. Fit simulator parameters and add a calibration-error table to
   `capacity/report.md`; do not extrapolate capacity before this gate.
3. Complete the dashboard and the remaining end-to-end kind/restore drills in
   an environment that can host them.
