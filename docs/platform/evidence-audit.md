# Evidence-label audit — 2026-11-02

## Scope

Audited the platform specification, interfaces, ADRs, diagrams, operations
runbook, capacity report and committed tips of `s2/simulator`, `s3/dataplane`
and `s4/enterprise-ui`.

## Rules checked

| Rule | Result |
|---|---|
| Simulator results are labelled SIMULATED | Pass: docs state uncalibrated placeholders and pending GPU calibration |
| kind/inference-sim results distinguish platform from engine timing | Pass: labelled MEASURED (platform), engine timing simulated |
| Hardware sizing/extrapolation is gated by calibration | Pass: capacity report prohibits it before recorded error |
| Diagrams distinguish target behaviour | Pass: unfinished policy, offload, P/D, autoscaling and recovery nodes are dashed/TARGET |
| Unmerged committed work is not called integrated | Pass: spec and traceability identify worktree evidence |
| Uncommitted Helm and simulator changes are not evidence | Pass: no claim relies on dirty worktree state |
| Bounded kind load smoke is not a throughput benchmark | Pass: recorded only as MEASURED (platform), engine timing simulated |
| Scheduler shedding is not an engine-capacity result | Pass: documented as CPU-tested error translation and metric instrumentation only |
| Durable usage replay is not exactly-once transport | Pass: at-least-once stream delivery is paired with transactionally deduplicated persistence, subject to migration rollout |

## Pending evidence

- Real vLLM lease runs and a published calibration-error comparison.
- Real GPU KV saturation/offload and multimodal-engine smoke tests.
- Production-cluster restore/PVC and high-availability exercises.
- Capacity extrapolation from calibrated cells to user populations.
