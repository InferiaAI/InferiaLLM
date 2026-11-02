# Platform traceability matrix

| Deliverable | Primary artifact | Committed evidence | Boundary / next gate |
|---|---|---|---|
| Architecture and contracts | [spec.md](spec.md), [interfaces.md](interfaces.md), [ADRs](adr/README.md) | WS1 commits through `1b1849a` | Merge implementation worktrees before calling it integrated |
| Ten architecture diagrams | [diagrams/](diagrams/README.md) | DOT source + SVG render for all ten | Target-state portions remain explicitly labelled |
| Simulator | `src/simulator/` on `s2/simulator` | T1–T3 committed simulator slices | GPU calibration pending |
| llm-d data plane | `deploy/kind/`, `src/providers/k8s/llmd/` on `s3/dataplane` | kind demo, adapter and focused tests | CPU simulator backend; real vLLM validation pending |
| Async usage path | inference/gateway commits on `s3/dataplane` | Redis stream producer/consumer and regression tests | At-least-once delivery; production monitoring/runbook drill remains operational work |
| Self-hosted deployment | `deploy/helm/` on `s4/enterprise-ui` | Helm chart and optional backup CronJob committed | Cluster/PVC restore drill is environment-dependent |
| Enterprise extension seams | `src/common/extensions/` on `s4/enterprise-ui` | Protocol registry/defaults and tests committed | Projects, model policy and cost controls remain follow-on work |
| Capacity | [capacity/report.md](capacity/report.md) | CPU-only simulation and platform routing procedure | No GPU calibration; no capacity extrapolation |
| Operations | [ops.md](ops.md), [demo-runbook.md](demo-runbook.md) | CPU-safe runbooks | GPU and production-cluster drills are pending |

The branch names identify the committed source of evidence at this audit. They
do not assert that every implementation branch is merged into this checkout.
