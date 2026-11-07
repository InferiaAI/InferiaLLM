# 0005 — Three evidence tiers: MEASURED, SIMULATED, EXTRAPOLATED

## Context
The brief demands: "Clearly distinguish between simulated results, measured results, and extrapolated capacity estimates", and "Do not present simulated performance as actual GPU performance."

## Decision
Every published number carries exactly one label, plus a results manifest (`interfaces.md` §1):

| Tier | What it covers |
|---|---|
| MEASURED (real GPU) | vLLM on leased GPUs |
| MEASURED (platform), engine timing simulated | Real InferiaLLM + Envoy/EPP code on kind with `llm-d-inference-sim` |
| SIMULATED | `inferia-sim` |
| EXTRAPOLATED | Analytical scaling of the above |

Charts carry the label in the title or as a watermark. Results without a manifest are not reported.

## Alternatives considered
- **Report everything together with footnotes.** Rejected: too easy to misread, and it breaks the brief's explicit rule.

## Consequences
- More bookkeeping, mitigated by tooling: manifests are written automatically by the simulator and the lease scripts.
- Comparisons across tiers must be stated as such.

## Status
Accepted (2026-10-31).

## Sources
- `brief.md` Part A §7 and Phase 6
